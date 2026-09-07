"""Persistent CPU queue with owned leases and transactional result publication.

Each app worker drains SQLite independently. A heartbeat keeps long OCR passes
alive; expired attempts become explicit retryable failures, never silent reruns.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from ..analyse import AnalyseOptions, Capture, analyse
from ..domain.enums import Lane, Panel
from ..observability import REQUEST_ID, log_event
from ..rules.engine import RulesEngine
from ..rules.spec import RulePack
from .rescan import (
    RescanError,
    evidence_manifest,
    retained_date_context,
    snapshot_parent,
    validate_job,
    validate_target,
)

LOG = logging.getLogger("tula.jobs")
STAGES = {"received", "ocr", "extraction", "applicability", "measurement", "rules", "complete", "saved"}


class LeaseLost(RuntimeError):
    """This attempt may no longer change the job or publish its analysis."""


class InspectionJobs:
    def __init__(self, repo, rules, security=None, *, lease_seconds=900, heartbeat_seconds=30):
        if lease_seconds <= 0 or not 0 < heartbeat_seconds < lease_seconds:
            raise ValueError("Heartbeat interval must be positive and shorter than the job lease.")
        self.repo, self.rules, self.security = repo, rules, security
        self.lease_seconds, self.heartbeat_seconds = lease_seconds, heartbeat_seconds
        self.worker_id = uuid.uuid4().hex
        self._stop, self._wake = threading.Event(), threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread = None
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS inspection_job (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL,
                stage TEXT NOT NULL, detail TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at REAL NOT NULL, scan_id TEXT, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT, lease_token TEXT, lease_until REAL)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(inspection_job)")}
            for name, kind in (("lease_owner", "TEXT"), ("lease_token", "TEXT"), ("lease_until", "REAL")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE inspection_job ADD COLUMN {name} {kind}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_queue ON inspection_job(state,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_owner_queue ON inspection_job(owner_id,state,created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_created ON inspection_job(created_at DESC,id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_owner_created ON inspection_job(owner_id,created_at DESC,id)")

    def start(self):
        """Return False during a still-running shutdown; never revive its stop event."""
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return not self._stop.is_set()
            self._stop, self._wake = threading.Event(), threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(self._stop, self._wake),
                                            name="tula-inspection-worker", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=2):
        """Stop taking work; let the owned attempt finish. Return whether it stopped."""
        with self._lifecycle_lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
            if thread and thread is not threading.current_thread():
                thread.join(timeout=timeout)
            stopped = thread is None or not thread.is_alive()
        if not stopped:
            log_event(LOG, logging.WARNING, "worker_shutdown_pending")
        return stopped

    def _rule_snapshot(self, conn):
        """Pin selection and archive bytes under the same writer transaction as enqueue."""
        configured = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_configuration'").fetchone()
        selected = (conn.execute("SELECT value FROM app_configuration WHERE key='active_rule_version'").fetchone()
                    if configured else None)
        if selected:
            row = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (selected[0],)).fetchone()
            if row is None or hashlib.sha256(row["record"].encode()).hexdigest() != row["sha256"]:
                raise ValueError("The active rule archive is unavailable or failed its integrity check.")
            pack = RulePack.model_validate_json(row["record"])
            if pack.version != selected[0]:
                raise ValueError("The active rule archive has an inconsistent version.")
        else:
            pack = self.rules.pack.model_copy(deep=True)
        self.repo.archive_rules(pack, connection=conn)
        return pack.version

    def enqueue(self, captures, hashes, edits, *, actor, lane, complete, region="", geo=None, concerns=None,
                parent=None, legal_context=None, parent_revision=None, rescan_target=""):
        validate_target(rescan_target, parent)
        if parent_revision is not None and not parent:
            raise RescanError("An original revision must link to an inspection.", 422)
        if self.start() is False:
            raise ValueError("The inspection worker is shutting down. Retry after shutdown finishes.")
        job_id = uuid.uuid4().hex
        payload = {"captures": [asdict(c) for c in captures], "hashes": hashes, "edits": edits,
                   "operator": actor.display_name, "owner_id": str(actor.id), "lane": lane,
                   "complete": complete, "region": region[:150], "geo": list(geo) if geo else None,
                   "concerns": concerns or [], "parent": parent,
                   "legal_context": legal_context or {}, "rescan_target": rescan_target,
                   "request_id": REQUEST_ID.get() or None}
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._capacity(conn)
            original = None
            if parent:
                original, receipt = snapshot_parent(conn, parent, actor, parent_revision)
                prior_panels = {r["frame"]: r.get("panel", "unknown") for r in original.scan.capture_edits}
                if any(c.path in original.scan.frames for c in captures):
                    raise RescanError("Upload a new close-up; retained images are added automatically.", 422)
                captures = [Capture(path, Panel(prior_panels.get(path, "unknown")))
                            for path in original.scan.frames] + list(captures)
                if len(captures) > 12:
                    raise RescanError("A rescan can contain at most 12 retained and new images.", 422)
                hashes = {**original.scan.original_frame_hashes, **hashes}
                edits = original.scan.capture_edits + list(edits)
                payload.update(captures=[asdict(c) for c in captures], hashes=hashes, edits=edits,
                               parent_snapshot=receipt, rules_version=original.rules_version,
                               legal_context=retained_date_context(payload["legal_context"], original))
            else:
                payload["rules_version"] = self._rule_snapshot(conn)
            payload["expected_files"] = evidence_manifest(captures, hashes, parent=original)
            conn.execute("INSERT INTO inspection_job (id,owner_id,state,stage,detail,payload,created_at,updated_at) "
                         "VALUES (?,?,'queued','received','Images validated; waiting for the OCR worker',?,?,?)",
                         (job_id, str(actor.id), json.dumps(payload), datetime.now(UTC).isoformat(), time.time()))
        self._wake.set()
        return self.get(job_id)

    @staticmethod
    def _capacity(conn):
        waiting = conn.execute("SELECT COUNT(*) FROM inspection_job WHERE state IN ('queued','running')").fetchone()[0]
        if waiting >= 12:
            raise ValueError("The inspection queue is full. Retry when an active inspection finishes.")

    def get(self, job_id):
        with self.repo._connect() as conn:
            row = conn.execute("SELECT id,owner_id,state,stage,detail,created_at,updated_at,scan_id,error,attempts "
                               "FROM inspection_job WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_for(self, actor, *, state="unfinished", page=1, page_size=25):
        """List retained jobs within the same account scope as status/retry.

        Select only display fields from the bounded payloads. Evidence paths,
        transforms, rule archives and worker lease tokens are never returned.
        """
        filters = {"unfinished": ("queued", "running", "failed"), "all": (),
                   **{value: (value,) for value in ("queued", "running", "failed", "complete")}}
        if state not in filters:
            raise ValueError("Choose unfinished, queued, running, failed, completed or all processing jobs.")
        if type(page) is not int or not 1 <= page <= 1_000_000:
            raise ValueError("Choose a valid processing page number.")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Choose 1 to 100 processing jobs per page.")
        clauses, values = [], []
        if actor.role not in {"admin", "supervisor"}:
            clauses.append("owner_id=?")
            values.append(str(actor.id))
        scope = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.repo._connect() as conn:
            conn.execute("BEGIN")
            counts = {value: 0 for value in ("queued", "running", "failed", "complete")}
            counts.update({row[0]: row[1] for row in conn.execute(
                "SELECT state,COUNT(*) FROM inspection_job" + scope + " GROUP BY state", values)})
            selected = filters[state]
            if selected:
                clauses.append("state IN (" + ",".join("?" for _ in selected) + ")")
                values.extend(selected)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = conn.execute("SELECT COUNT(*) FROM inspection_job" + where, values).fetchone()[0]
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            rows = conn.execute(
                "SELECT id,owner_id,state,stage,detail,created_at,updated_at,scan_id,error,attempts,payload "
                "FROM inspection_job" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                [*values, page_size, (page - 1) * page_size]).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            raw_payload = item.pop("payload")
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            for key, limit in (("operator", 100), ("region", 150), ("lane", 30)):
                value = payload.get(key)
                item[key] = value[:limit] if isinstance(value, str) else ""
            captures = payload.get("captures")
            item["image_count"] = len(captures) if isinstance(captures, list) else None
            result.append(item)
        return {"rows": result, "counts": counts, "total": total, "page": page,
                "pages": pages, "page_size": page_size, "state": state}

    def retry(self, job_id, *, actor=None):
        if self.start() is False:
            raise ValueError("The inspection worker is shutting down. Retry after shutdown finishes.")
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._capacity(conn)
            row = conn.execute("SELECT payload FROM inspection_job WHERE id=? AND state='failed'", (job_id,)).fetchone()
            if row:
                validate_job(conn, json.loads(row["payload"]), actor=actor)
            changed = conn.execute("UPDATE inspection_job SET state='queued',stage='received',"
                                   "detail='Retry queued',error=NULL,updated_at=?,lease_owner=NULL,"
                                   "lease_token=NULL,lease_until=NULL WHERE id=? AND state='failed'",
                                   (time.time(), job_id)).rowcount
        if not changed:
            raise ValueError("Only failed inspections can be retried.")
        self._wake.set()
        return self.get(job_id)

    def _claim(self, job_id=None):
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            expired = conn.execute("SELECT id FROM inspection_job WHERE state='running' AND "
                                   "(lease_until<=? OR (lease_until IS NULL AND updated_at<?))",
                                   (now, now - self.lease_seconds)).fetchall()
            conn.executemany("UPDATE inspection_job SET state='failed',error='The worker stopped responding. Retry this saved upload.',"
                             "detail='Processing interrupted',updated_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL "
                             "WHERE id=?", [(now, row[0]) for row in expired])
            query = "SELECT * FROM inspection_job WHERE state='queued'"
            row = conn.execute(query + (" AND id=?" if job_id else " ORDER BY created_at,id LIMIT 1"),
                               (job_id,) if job_id else ()).fetchone()
            claimed = None
            if row:
                token = uuid.uuid4().hex
                conn.execute("UPDATE inspection_job SET state='running',attempts=attempts+1,updated_at=?,"
                             "lease_owner=?,lease_token=?,lease_until=? WHERE id=? AND state='queued'",
                             (now, self.worker_id, token, now + self.lease_seconds, row["id"]))
                claimed = dict(conn.execute("SELECT * FROM inspection_job WHERE id=?", (row["id"],)).fetchone())
        for row in expired:
            log_event(LOG, logging.WARNING, "inspection_lease_expired", job_id=row[0])
        return claimed

    def _renew(self, job, *, stage=None, detail=None):
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            assignments, values = "lease_until=?,updated_at=?", [now + self.lease_seconds, now]
            if stage is not None:
                assignments += ",stage=?,detail=?"
                values += [stage, detail]
            changed = conn.execute(f"UPDATE inspection_job SET {assignments} WHERE id=? AND state='running' "
                                   "AND lease_owner=? AND lease_token=? AND lease_until>?",
                                   (*values, job["id"], self.worker_id, job["lease_token"], now)).rowcount
        return bool(changed)

    def _progress(self, job, stage, detail, timing=None):
        if not self._renew(job, stage=stage, detail=detail):
            raise LeaseLost()
        now = time.perf_counter()
        fields = {}
        if timing is not None:
            fields = {
                "stage_elapsed_ms": round((now - timing["last"]) * 1000, 2),
                "total_elapsed_ms": round((now - timing["started"]) * 1000, 2),
            }
            timing["last"] = now
        log_event(
            LOG,
            logging.INFO,
            "inspection_stage",
            job_id=job["id"],
            stage=stage if stage in STAGES else "other",
            attempt=job["attempts"],
            request_id=timing.get("request_id") if timing else None,
            **fields,
        )

    def _heartbeat(self, job, done):
        while not done.wait(self.heartbeat_seconds):
            try:
                if not self._renew(job):
                    return
            except sqlite3.Error as exc:
                log_event(
                    LOG,
                    logging.WARNING,
                    "inspection_heartbeat_failed",
                    job_id=job["id"],
                    error_type=type(exc).__name__,
                )

    def _loop(self, stop, wake):
        while not stop.is_set():
            try:
                job = self._claim()
                if job:
                    self._run(job)
                    continue
            except Exception as exc:  # noqa: BLE001 - keep the durable worker alive without logging label data.
                log_event(
                    LOG, logging.ERROR, "queue_poll_failed", error_type=type(exc).__name__
                )
            wake.wait(1)
            wake.clear()

    def _publish(self, job, result):
        """The lease fence, complete record and job state commit or roll back together."""
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute("SELECT 1 FROM inspection_job WHERE id=? AND state='running' "
                                 "AND lease_owner=? AND lease_token=? AND lease_until>?",
                                 (job["id"], self.worker_id, job["lease_token"], time.time())).fetchone()
            if not owner:
                raise LeaseLost()
            validate_job(conn, json.loads(job["payload"]))
            self.repo.save(result, connection=conn)
            conn.execute("UPDATE inspection_job SET state='complete',stage='saved',detail='Inspection saved',"
                         "scan_id=?,updated_at=?,error=NULL,lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE id=?",
                         (result.scan.scan_id, time.time(), job["id"]))

    def _run(self, job):
        # Explicit execution of a queued row still requires an atomic claim.
        if not job.get("lease_token"):
            job = self._claim(job["id"])
            if job is None:
                return
        jid = job["id"]
        if job.get("lease_owner") != self.worker_id or not self._renew(job):
            return
        started = time.perf_counter()
        timing = {"started": started, "last": started}
        done = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job, done), daemon=True,
                                     name="tula-job-heartbeat")
        heartbeat.start()
        try:
            p = json.loads(job["payload"])
            timing["request_id"] = p.get("request_id")
            log_event(
                LOG,
                logging.INFO,
                "inspection_started",
                job_id=jid,
                attempt=job["attempts"],
                request_id=timing["request_id"],
                rules_version=p.get("rules_version"),
                frames=len(p["captures"]) if isinstance(p.get("captures"), list) else None,
            )
            with self.repo._connect() as conn:
                parent = validate_job(conn, p)
            # Recover the old save-before-complete crash window without repeating OCR.
            result = self.repo.get(job.get("scan_id") or jid[:10].upper())
            if result is None:
                if not p.get("rules_version"):
                    raise ValueError("The job has no retained rule version. A new inspection is required.")
                archived = self.repo.archived_rules(p["rules_version"])
                if archived is None:
                    raise ValueError("The rule version assigned to this job is unavailable.")
                selected_rules = RulesEngine(RulePack.model_validate(archived))
                if selected_rules.pack.version != p["rules_version"]:
                    raise ValueError("The rule archive version does not match the queued job.")
                captures = [Capture(c["path"], Panel(c["panel"])) for c in p["captures"]]
                result = analyse(captures, AnalyseOptions(
                    lane=Lane(p["lane"]), operator=p["operator"], engine_name="rapidocr",
                    geo=tuple(p["geo"]) if p.get("geo") else None,
                    capture_is_complete=p["complete"], allergen_concerns=p["concerns"],
                    packing_date=parent.scan.packing_date if parent else None,
                    progress=lambda stage, detail: self._progress(job, stage, detail, timing),
                    legal_context=p.get("legal_context", {})), rules=selected_rules)
                result.scan.scan_id = jid[:10].upper()
                result.findings = [f.model_copy(update={"finding_id": f"F-{result.scan.scan_id}-{i:03d}"})
                                   for i, f in enumerate(result.findings)]
                result.scan.inspector_id = p["owner_id"]
                result.scan.original_frame_hashes = p["hashes"]
                result.scan.capture_edits = p["edits"]
                result.scan.region = p["region"]
                result.scan.parent_scan_id = p["parent"]
                if parent:
                    result.scan.parent_revision = parent.review.revision
                    result.scan.parent_record_sha256 = p["parent_snapshot"]["record_sha256"]
                    result.scan.rescan_target = p.get("rescan_target", "")
            self._publish(job, result)
            log_event(
                LOG,
                logging.INFO,
                "inspection_completed",
                job_id=jid,
                scan_id=result.scan.scan_id,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                rules_version=result.rules_version,
                frames=len(result.scan.frames),
                request_id=timing.get("request_id"),
            )
            if self.security:
                try:
                    self.security.audit(actor_id=p["owner_id"], action="inspection.analysed",
                                        entity_type="inspection", entity_id=result.scan.scan_id,
                                        after={"rules_version": result.rules_version, "frames": len(result.scan.frames)})
                except Exception as exc:  # noqa: BLE001 - isolate external audit sinks from committed results.
                    # A committed result must not become retryable when a separate audit sink fails.
                    log_event(
                        LOG,
                        logging.ERROR,
                        "inspection_audit_failed",
                        job_id=jid,
                        error_type=type(exc).__name__,
                        request_id=timing.get("request_id"),
                    )
        except LeaseLost:
            log_event(
                LOG,
                logging.WARNING,
                "inspection_lease_lost",
                job_id=jid,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                request_id=timing.get("request_id"),
            )
        except Exception as exc:  # noqa: BLE001 - persist unexpected model failures without leaking their text.
            log_event(
                LOG,
                logging.ERROR,
                "inspection_failed",
                job_id=jid,
                error_type=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                request_id=timing.get("request_id"),
            )
            with self.repo._connect() as conn:
                conn.execute("UPDATE inspection_job SET state='failed',detail='Analysis could not finish',error=?,"
                             "updated_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE id=? AND state='running' "
                             "AND lease_owner=? AND lease_token=? AND lease_until>?",
                             (str(exc) if isinstance(exc, RescanError) else
                              "The analysis could not finish. Your images are saved. Retry, or capture a clearer label if the problem persists.",
                              time.time(), jid, self.worker_id, job["lease_token"], time.time()))
        finally:
            done.set()
            heartbeat.join(timeout=2)
