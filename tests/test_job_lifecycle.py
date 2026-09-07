"""Real SQLite/thread/ASGI lifecycle tests; analysis is an explicit CPU test stub.

These verify ownership and durability, not OCR accuracy. Generated image files
and their hashes are retained in the same way as a submitted capture.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tula.analyse import Capture
from tula.domain.enums import Panel
from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file
from tula.observability import REQUEST_ID
from tula.rules.engine import RulesEngine
from tula.services import jobs as jobs_module
from tula.services.jobs import InspectionJobs, LeaseLost
from tula.storage.db import Repository
from tula.web.lifecycle import get_jobs, install_job_lifecycle

OWNER = SimpleNamespace(id="lifecycle-test-owner", display_name="Lifecycle Test Inspector")


def analysis_stub(captures, options, *, rules):
    return Analysis(scan=Scan(scan_id="temporary", frames=[c.path for c in captures],
                              frame_hashes={c.path: sha256_file(c.path) for c in captures},
                              operator=options.operator), package=PackageFacts(),
                    engine="explicit-lifecycle-test-stub", rules_version=rules.pack.version)


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "jobs.db")
    rules = RulesEngine.from_directory()
    frame = tmp_path / "generated-label.png"
    Image.new("RGB", (80, 60), "white").save(frame)
    captures = [Capture(str(frame), Panel.PDP)]
    queue = InspectionJobs(repo, rules)
    monkeypatch.setattr(queue, "start", lambda: None)
    monkeypatch.setattr(jobs_module, "analyse", analysis_stub)
    value = SimpleNamespace(repo=repo, rules=rules, queue=queue, captures=captures,
                            hashes={str(frame): sha256_file(frame)}, workers=[queue])
    yield value
    for worker in value.workers:
        worker.stop()


def queued(case):
    return case.queue.enqueue(case.captures, case.hashes, [], actor=OWNER, lane="field", complete=False)


def test_http_correlation_is_retained_for_background_stage_events(case, caplog):
    request_id = "upload-request-123456"
    token = REQUEST_ID.set(request_id)
    try:
        pending = queued(case)
    finally:
        REQUEST_ID.reset(token)
    with case.repo._connect() as conn:
        payload = json.loads(conn.execute(
            "SELECT payload FROM inspection_job WHERE id=?", (pending["id"],)
        ).fetchone()[0])
    assert payload["request_id"] == request_id
    queue = worker(case)
    with caplog.at_level(logging.INFO, logger="tula.jobs"):
        queue._run(queue._claim(pending["id"]))
    events = [record for record in caplog.records if record.name == "tula.jobs"]
    started = next(record for record in events if record.event == "inspection_started")
    completed = next(record for record in events if record.event == "inspection_completed")
    assert started.request_id == completed.request_id == request_id
    assert started.frames == 1 and started.rules_version == case.rules.pack.version
    assert completed.duration_ms >= 0


def worker(case, **kwargs):
    queue = InspectionJobs(Repository(case.repo.path), case.rules, **kwargs)
    case.workers.append(queue)
    return queue


def wait_for(queue, job_id, state="complete", timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        record = queue.get(job_id)
        if record and record["state"] == state:
            return record
        time.sleep(.01)
    pytest.fail(f"Job failed to reach {state}: {queue.get(job_id)}")


def test_asgi_startup_drains_persisted_queue_without_any_request_and_preserves_lifespan(case):
    pending = queued(case)
    hooks = []

    @asynccontextmanager
    async def prior(app):
        hooks.append("startup")
        yield {"prior_state": True}
        hooks.append("shutdown")

    app = FastAPI(lifespan=prior)
    web = SimpleNamespace(app=app, repo=Repository(case.repo.path), rules=case.rules)
    install_job_lifecycle(web)
    installed = app.router.lifespan_context
    install_job_lifecycle(web)
    assert app.router.lifespan_context is installed
    with TestClient(app):
        saved = wait_for(app.state.jobs, pending["id"])
        assert hooks == ["startup"]
        assert app.state.jobs._thread.is_alive()
    assert hooks == ["startup", "shutdown"]
    assert not app.state.jobs._thread.is_alive()
    assert case.repo.get(saved["scan_id"]).scan.original_frame_hashes == case.hashes
    with TestClient(app):
        assert app.state.jobs.get(pending["id"])["attempts"] == 1
    assert not app.state.jobs._thread.is_alive()


def test_lifespan_shutdown_also_runs_when_application_body_raises(case):
    app = FastAPI()
    web = SimpleNamespace(app=app, repo=case.repo, rules=case.rules)
    install_job_lifecycle(web)
    with pytest.raises(RuntimeError, match="test body"), TestClient(app):
        raise RuntimeError("test body")
    assert not app.state.jobs._thread.is_alive()


def test_startup_recovers_expired_attempt_without_reprocessing_failed_or_complete_jobs(case, monkeypatch):
    expired, failed, completed = [queued(case) for _ in range(3)]
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='running',stage='ocr',lease_owner='stopped-process',"
                     "lease_token='stopped-attempt',lease_until=?,attempts=1 WHERE id=?",
                     (time.time() - 1, expired["id"]))
        conn.execute("UPDATE inspection_job SET state='failed',error='retained failure' WHERE id=?", (failed["id"],))
        conn.execute("UPDATE inspection_job SET state='complete',scan_id='RETAINED-SCAN' WHERE id=?", (completed["id"],))
        before = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM inspection_job")}
    calls = []
    monkeypatch.setattr(jobs_module, "analyse", lambda *args, **kwargs: calls.append(1))
    app = FastAPI()
    web = SimpleNamespace(app=app, repo=case.repo, rules=case.rules)
    install_job_lifecycle(web)
    with TestClient(app):
        interrupted = wait_for(app.state.jobs, expired["id"], "failed")
    assert interrupted["stage"] == "ocr" and interrupted["attempts"] == 1
    assert "Retry" in interrupted["error"] and calls == []
    with case.repo._connect() as conn:
        after = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM inspection_job")}
    assert after[failed["id"]] == before[failed["id"]]
    assert after[completed["id"]] == before[completed["id"]]
    assert after[expired["id"]]["payload"] == before[expired["id"]]["payload"]
    assert all(sha256_file(path) == digest for path, digest in case.hashes.items())


def test_concurrent_factory_and_worker_start_create_one_thread(case):
    web = SimpleNamespace(app=FastAPI(), repo=case.repo, rules=case.rules)
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: get_jobs(web), range(16)))
    assert len({id(value) for value in instances}) == 1
    queue = instances[0]
    case.workers.append(queue)
    first = queue._thread
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda _: queue.start(), range(16)))
    assert queue._thread is first


def test_stop_timeout_cannot_clear_old_generation_or_start_second_analysis(case, monkeypatch):
    pending = queued(case)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(captures, options, *, rules):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", blocked)
    queue = worker(case)
    queue.start()
    try:
        assert entered.wait(3)
        original_thread, original_stop = queue._thread, queue._stop
        assert queue.stop(timeout=.01) is False
        assert queue.start() is False
        assert queue._thread is original_thread and original_stop.is_set()
        with pytest.raises(ValueError, match="shutting down"):
            queue.enqueue([], {}, [], actor=OWNER, lane="field", complete=False)
    finally:
        release.set()
    original_thread.join(3)
    assert not original_thread.is_alive()
    wait_for(queue, pending["id"])
    assert queue.start() is True and queue._thread is not original_thread
    assert queue._stop is not original_stop and original_stop.is_set()
    assert calls == [1]


def test_two_workers_atomically_claim_distinct_jobs_once(case, monkeypatch):
    pending = [queued(case) for _ in range(6)]
    calls, lock = [], threading.Lock()
    barrier = threading.Barrier(2)

    def controlled(captures, options, *, rules):
        with lock:
            index = len(calls)
            calls.append(threading.current_thread().ident)
        if index < 2:
            barrier.wait(timeout=4)
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", controlled)
    first, second = worker(case), worker(case)
    first.start()
    second.start()
    saved = [wait_for(first, job["id"]) for job in pending]
    assert len(calls) == 6 and len(set(calls)) == 2
    assert all(row["attempts"] == 1 for row in saved)
    assert len(case.repo.search()) == 6
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection_revision").fetchone()[0] == 6


def test_heartbeat_retains_slow_pass_without_fabricated_stage_updates(case, monkeypatch):
    pending = queued(case)
    entered, release = threading.Event(), threading.Event()

    def slow(captures, options, *, rules):
        options.progress("ocr", "Waiting inside a real queue test analysis stub")
        entered.set()
        assert release.wait(5)
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", slow)
    queue = worker(case, lease_seconds=.5, heartbeat_seconds=.05)
    observer = worker(case, lease_seconds=.5, heartbeat_seconds=.05)
    queue.start()
    try:
        assert entered.wait(3)
        initial = queue.get(pending["id"])
        # Wait more than one complete lease duration while another worker scans.
        end = time.monotonic() + .8
        while time.monotonic() < end:
            assert observer._claim() is None
            time.sleep(.04)
        live = queue.get(pending["id"])
        assert live["state"] == "running" and live["stage"] == initial["stage"] == "ocr"
        assert live["detail"] == initial["detail"] and live["updated_at"] > initial["updated_at"]
    finally:
        release.set()
    assert wait_for(queue, pending["id"])["attempts"] == 1


@pytest.mark.parametrize("stale_outcome", ["result", "failure", "progress"])
def test_reclaimed_job_rejects_old_attempt_completion_failure_and_progress(case, monkeypatch, stale_outcome):
    pending = queued(case)
    first, second = worker(case), worker(case)
    stale = first._claim(pending["id"])
    entered, release = threading.Event(), threading.Event()

    def controlled(captures, options, *, rules):
        if threading.current_thread().name == "expired-test-attempt":
            entered.set()
            assert release.wait(5)
            if stale_outcome == "failure":
                raise RuntimeError("old attempt failure")
            if stale_outcome == "progress":
                options.progress("rules", "Old attempt must not overwrite the saved stage")
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", controlled)
    thread = threading.Thread(target=first._run, args=(stale,), name="expired-test-attempt")
    thread.start()
    try:
        assert entered.wait(3)
        with case.repo._connect() as conn:
            conn.execute("UPDATE inspection_job SET lease_until=? WHERE id=?", (time.time() - 1, pending["id"]))
        assert second._claim() is None
        assert second.get(pending["id"])["state"] == "failed"
        monkeypatch.setattr(second, "start", lambda: None)
        second.retry(pending["id"])
        current = second._claim(pending["id"])
        assert current["lease_token"] != stale["lease_token"]
        second._run(current)
        saved = second.get(pending["id"])
        before = case.repo.get(saved["scan_id"]).model_dump(mode="json")
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert second.get(pending["id"]) == saved
    assert case.repo.get(saved["scan_id"]).model_dump(mode="json") == before
    assert len(case.repo.revisions(saved["scan_id"])) == 1
    assert saved["attempts"] == 2


def test_expired_attempt_cannot_publish_even_before_another_worker_recovers_it(case):
    pending = queued(case)
    queue = worker(case)
    claimed = queue._claim(pending["id"])
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET lease_until=? WHERE id=?", (time.time() - 1, pending["id"]))
    result = analysis_stub(case.captures, SimpleNamespace(operator="Test"), rules=case.rules)
    with pytest.raises(LeaseLost):
        queue._publish(claimed, result)
    assert not queue._renew(claimed)
    assert case.repo.search() == []


def test_heartbeat_rechecks_time_after_waiting_for_sqlite_writer(case):
    pending = queued(case)
    queue = worker(case, lease_seconds=.3, heartbeat_seconds=.05)
    claimed = queue._claim(pending["id"])
    entered = threading.Event()

    def renew_after_lock():
        entered.set()
        return queue._renew(claimed)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with case.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            future = pool.submit(renew_after_lock)
            assert entered.wait(2)
            # The caller begins renewal before expiry, but acquires SQLite after it.
            time.sleep(.4)
        assert future.result(timeout=3) is False
    with case.repo._connect() as conn:
        retained = dict(conn.execute("SELECT * FROM inspection_job WHERE id=?", (pending["id"],)).fetchone())
    assert retained["lease_until"] == claimed["lease_until"]


def test_result_save_and_job_completion_roll_back_in_same_transaction(case, monkeypatch):
    pending = queued(case)
    queue = worker(case)
    claimed = queue._claim(pending["id"])
    original_save = queue.repo.save

    def write_then_fail(result, *, connection=None):
        assert connection is not None
        original_save(result, connection=connection)
        assert connection.execute("SELECT COUNT(*) FROM inspection").fetchone()[0] == 1
        raise RuntimeError("Simulated failure after all inspection and revision writes")

    monkeypatch.setattr(queue.repo, "save", write_then_fail)
    queue._run(claimed)
    assert queue.get(pending["id"])["state"] == "failed"
    with case.repo._connect() as conn:
        for table in ("inspection", "finding", "inspection_revision"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert all(sha256_file(path) == digest for path, digest in case.hashes.items())


def test_failed_completion_statement_also_rolls_back_saved_analysis(case):
    pending = queued(case)
    queue = worker(case)
    claimed = queue._claim(pending["id"])
    with case.repo._connect() as conn:
        conn.execute("CREATE TRIGGER fail_job_completion BEFORE UPDATE OF state ON inspection_job "
                     "WHEN NEW.state='complete' BEGIN SELECT RAISE(ABORT, 'test completion failure'); END")
    queue._run(claimed)
    assert queue.get(pending["id"])["state"] == "failed"
    assert case.repo.search() == []
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection_revision").fetchone()[0] == 0


def test_committed_result_is_not_failed_or_reprocessed_when_audit_sink_fails(case, monkeypatch, caplog):
    pending = queued(case)
    calls = []

    def failing_audit(**kwargs):
        raise RuntimeError("private audit detail")

    def counting(captures, options, *, rules):
        calls.append(1)
        options.progress("ocr", "PRIVATE LABEL TRANSCRIPTION")
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", counting)
    queue = worker(case, security=SimpleNamespace(audit=failing_audit))
    with caplog.at_level(logging.INFO, logger="tula.jobs"):
        queue._run(queue._claim(pending["id"]))
    saved = queue.get(pending["id"])
    assert saved["state"] == "complete" and saved["error"] is None
    assert case.repo.get(saved["scan_id"])
    queue._run({"id": pending["id"]})
    assert calls == [1]
    assert "PRIVATE LABEL" not in caplog.text and "private audit detail" not in caplog.text
    stages = [record for record in caplog.records if getattr(record, "event", None) == "inspection_stage"]
    assert len(stages) == 1 and stages[0].stage == "ocr" and stages[0].job_id == pending["id"]
    assert stages[0].stage_elapsed_ms >= 0 and stages[0].total_elapsed_ms >= 0
    assert json.loads(stages[0].getMessage())["stage"] == "ocr"
    assert any(getattr(record, "event", None) == "inspection_audit_failed" for record in caplog.records)


def test_rule_reference_read_once_and_archive_matches_payload_even_when_selection_changes(case, monkeypatch):
    pack = case.rules.pack.model_copy(deep=True, update={"version": "snapshot-original"})
    replacement = case.rules.pack.model_copy(deep=True, update={"version": "snapshot-replacement"})
    reads = []

    class ChangingReference:
        @property
        def pack(self):
            reads.append(1)
            return pack if len(reads) == 1 else replacement

    case.queue.rules = ChangingReference()
    pending = queued(case)
    with case.repo._connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM inspection_job WHERE id=?", (pending["id"],)).fetchone()[0])
    assert reads == [1]
    assert payload["rules_version"] == "snapshot-original"
    assert case.repo.archived_rules("snapshot-original")["version"] == "snapshot-original"
    assert case.repo.archived_rules("snapshot-replacement") is None


def test_persisted_rule_selection_overrides_stale_process_reference(case):
    pack = case.rules.pack.model_copy(deep=True, update={"version": "other-process-active"})
    case.repo.archive_rules(pack)
    with case.repo._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_configuration (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO app_configuration (key,value) VALUES ('active_rule_version',?)", (pack.version,))
    pending = queued(case)
    assert case.queue.rules.pack.version != pack.version
    queue = worker(case)
    queue._run(queue._claim(pending["id"]))
    saved = queue.get(pending["id"])
    assert case.repo.get(saved["scan_id"]).rules_version == pack.version


def test_corrupt_active_rule_archive_rejects_enqueue_without_partial_job(case):
    case.repo.archive_rules(case.rules.pack)
    with case.repo._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_configuration (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO app_configuration (key,value) VALUES ('active_rule_version',?)", (case.rules.pack.version,))
        conn.execute("UPDATE rule_version SET record='{}'")
    with pytest.raises(ValueError, match="integrity"):
        queued(case)
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection_job").fetchone()[0] == 0


def test_legacy_schema_upgrade_preserves_queued_failed_and_completed_evidence(tmp_path):
    repo = Repository(tmp_path / "old.db")
    with sqlite3.connect(repo.path) as conn:
        conn.execute("CREATE TABLE inspection_job (id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,state TEXT NOT NULL,"
                     "stage TEXT NOT NULL,detail TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL,"
                     "updated_at REAL NOT NULL,scan_id TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0)")
        for state in ("queued", "failed", "complete"):
            conn.execute("INSERT INTO inspection_job VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (state, OWNER.id, state, "ocr", "retained detail", '{"evidence":"retained"}',
                          "2026-09-07", time.time(), "PRIOR" if state == "complete" else None,
                          "retained error" if state == "failed" else None, 2))
    queue = InspectionJobs(repo, RulesEngine.from_directory())
    with repo._connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM inspection_job ORDER BY id")]
    assert [row["state"] for row in rows] == ["complete", "failed", "queued"]
    assert all(row["payload"] == '{"evidence":"retained"}' and row["attempts"] == 2 for row in rows)
    assert all(row["lease_token"] is None for row in rows)
    assert queue.get("complete")["scan_id"] == "PRIOR"
