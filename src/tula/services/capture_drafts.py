"""Private, explicitly saved capture work; never an inspection or queued job."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import time
from pathlib import Path

from PIL import Image

from ..domain.enums import Lane
from ..domain.models import sha256_file, validated_geo
from .capture import CaptureError, receive
from .context import BUNDLES, CATEGORIES, SHAPES
from .rescan import RescanError, current_parent, validate_target

MAX_DRAFT_BYTES = 100 * 1024 * 1024
MAX_ACCOUNT_BYTES = 300 * 1024 * 1024
MAX_DRAFTS = 10
RETENTION_SECONDS = 30 * 24 * 60 * 60
_ID = re.compile(r"^[a-f0-9]{32}$")


class DraftError(CaptureError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DraftError("This capture draft link is invalid.", 404)
    return value


def _state(raw):
    if not isinstance(raw, dict) or set(raw) - {
        "title", "lane", "region", "concerns", "context", "dimensions", "parent_scan_id", "complete",
        "parent_revision", "rescan_target", "geo"
    }:
        raise DraftError("Provide valid capture draft details.")
    result = {}
    for key, limit in (("title", 100), ("region", 150), ("concerns", 600), ("parent_scan_id", 100)):
        value = raw.get(key, "")
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
            raise DraftError(f"{key.replace('_', ' ').capitalize()} must be text of at most {limit} characters.")
        result[key] = value.strip()
    revision = raw.get("parent_revision")
    if revision is not None and (type(revision) is not int or revision < 0 or not result["parent_scan_id"]):
        raise DraftError("Provide a valid revision for the linked inspection.")
    result["parent_revision"] = revision
    try:
        result["rescan_target"] = validate_target(raw.get("rescan_target", ""), result["parent_scan_id"])
    except RescanError as exc:
        raise DraftError(str(exc), exc.status) from None
    try:
        result["lane"] = Lane(raw.get("lane", "field")).value
    except (ValueError, TypeError):
        raise DraftError("Choose a valid capture source.") from None
    context = raw.get("context", {})
    if not isinstance(context, dict) or set(context) - {
        "category", "bundle_type", "shape", "is_imported", "category_confirmed",
        "bundle_confirmed", "shape_confirmed", "imported_confirmed"
    }:
        raise DraftError("Provide valid package context selections.")
    result["context"] = {}
    for name, choices in (("category", CATEGORIES), ("bundle_type", BUNDLES), ("shape", SHAPES)):
        value = context.get(name, "unknown")
        if not isinstance(value, str) or value not in choices:
            raise DraftError(f"Choose a valid {name.replace('_', ' ')}.")
        result["context"][name] = value
    if "is_imported" in context:
        if type(context["is_imported"]) is not bool:
            raise DraftError("Origin context must be imported, domestic or unknown.")
        result["context"]["is_imported"] = context["is_imported"]
    # Attestations deliberately never cross the save/resume boundary.
    result["complete"] = False
    for flag in ("category_confirmed", "bundle_confirmed", "shape_confirmed", "imported_confirmed"):
        result["context"][flag] = False
    dimensions = raw.get("dimensions", {})
    if not isinstance(dimensions, dict) or set(dimensions) - {"pdp_width_mm", "pdp_height_mm"}:
        raise DraftError("Provide valid panel dimensions.")
    for value in dimensions.values():
        if type(value) not in (int, float) or not 1 <= value <= 5000 or not math.isfinite(value):
            raise DraftError("Panel dimensions must be between 1 and 5,000 mm.")
    result["dimensions"] = dimensions  # A draft may have only one dimension so far.
    try:
        geo = validated_geo(raw.get("geo"))
    except ValueError as exc:
        raise DraftError(str(exc)) from None
    result["geo"] = list(geo) if geo else None
    return result


class CaptureDrafts:
    def __init__(self, repo, root, security, *, clock=time.time):
        self.repo, self.security, self.clock = repo, security, clock
        self.root = (Path(root).resolve() / "data" / "capture-drafts")
        self.root.mkdir(parents=True, exist_ok=True)
        with repo._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS capture_draft (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision INTEGER NOT NULL,
                state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                expires_at REAL NOT NULL, total_bytes INTEGER NOT NULL,
                save_token TEXT NOT NULL, fingerprint TEXT NOT NULL, record TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capture_draft_owner ON capture_draft(owner_id,state)")

    def _audit(self, conn, user_id, action, draft_id, before=None, after=None):
        self.security._audit(conn, actor_id=user_id, action=action, entity_type="capture_draft",
                             entity_id=draft_id, before=before, after=after)

    def _row(self, conn, draft_id, user):
        row = conn.execute("SELECT * FROM capture_draft WHERE id=? AND owner_id=?", (draft_id, user.id)).fetchone()
        if row is None or row["state"] != "active" or row["expires_at"] <= self.clock():
            raise DraftError("This capture draft is unavailable or has expired.", 404)
        return row

    def _parent(self, conn, details, user):
        parent_id = details["parent_scan_id"]
        if not parent_id:
            return 0
        try:
            analysis = current_parent(conn, parent_id, user, details.get("parent_revision"))
        except RescanError as exc:
            raise DraftError(str(exc), 409 if exc.status == 404 else exc.status) from None
        details["parent_revision"] = analysis.review.revision
        return len(analysis.scan.frames)

    def _original(self, record, index):
        session = self.root / _identifier(record["session"])
        source = session / f"{index:02d}-original.bin"
        if session.is_symlink() or source.is_symlink() or session.resolve().parent != self.root.resolve():
            raise DraftError("Saved original image could not be verified.", 409)
        return source

    def _verified(self, record, index):
        image = record["images"][index]
        source = self._original(record, index)
        try:
            if not source.is_file() or source.stat().st_size != image["bytes"] or sha256_file(str(source)) != image["sha256"]:
                raise DraftError("A saved original image is missing or changed. This draft cannot be resumed.", 409)
        except OSError:
            raise DraftError("A saved original image cannot be read. This draft cannot be resumed.", 409) from None
        return source

    def _public(self, row, *, detail=False, prior_count=0, details=None):
        record = json.loads(row["record"])
        result = {key: row[key] for key in ("id", "revision", "created_at", "updated_at", "expires_at", "total_bytes")}
        result.update(title=record["details"]["title"] or "Untitled package capture", image_count=len(record["images"]))
        if detail:
            result.update(details=details or record["details"], prior_count=prior_count,
                          images=[{**item, "url": f"/v1/capture/drafts/{row['id']}/images/{index}?revision={row['revision']}"}
                                  for index, item in enumerate(record["images"])])
        return result

    def list(self, user):
        self.cleanup()
        with self.repo._connect() as conn:
            rows = conn.execute("SELECT * FROM capture_draft WHERE owner_id=? AND state='active' AND expires_at>? ORDER BY updated_at DESC",
                                (user.id, self.clock())).fetchall()
            return [self._public(row) for row in rows]

    def get(self, draft_id, user):
        _identifier(draft_id)
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, draft_id, user)
            record = json.loads(row["record"])
            prior_count = self._parent(conn, record["details"], user)
            if prior_count + len(record["images"]) > 12:
                raise DraftError("The linked inspection now exceeds the 12-image limit.", 409)
            for index in range(len(record["images"])):
                self._verified(record, index)
            return self._public(row, detail=True, prior_count=prior_count, details=record["details"])

    def image(self, draft_id, index, revision, user):
        _identifier(draft_id)
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, draft_id, user)
            if row["revision"] != revision:
                raise DraftError("This draft was updated in another tab. Reload its latest saved version.", 409)
            record = json.loads(row["record"])
            if index < 0 or index >= len(record["images"]):
                raise DraftError("No such draft image.", 404)
            self._parent(conn, record["details"], user)
            # Read under the same lock as revision checks: delete/update cannot
            # remove this source halfway through an authenticated download.
            return self._verified(record, index).read_bytes(), record["images"][index]

    def save(self, draft_id, revision, save_token, user, files, *, details, panels="pdp", edits="[]"):
        _identifier(draft_id)
        _identifier(save_token)
        if type(revision) is not int or revision < 0:
            raise DraftError("Provide the draft's saved revision.")
        details = _state(details)
        if len(files) > 12 or sum(file.size or 0 for file in files) > MAX_DRAFT_BYTES:
            raise DraftError("A draft can hold up to 12 images and 100 MB of originals.", 413)
        if len(edits) > 12000 or len(panels) > 200:
            raise DraftError("Image edits are too large.")
        self.cleanup()
        # Each attempt gets a private immutable directory. No writer edits the
        # originals of a saved revision, including an idempotent retry.
        _, hashes, captures = receive(files, self.root, panels=panels, edits=edits)
        session = Path(captures[0]["original"]).parent.name
        record = {"session": session, "details": details, "images": []}
        old_session = None
        committed = False
        try:
            for index, (upload, capture) in enumerate(zip(files, captures, strict=True)):
                original = self._original(record, index)
                with Image.open(original) as image:
                    mime = Image.MIME.get(image.format, "application/octet-stream")
                filename = Path((upload.filename or "package-image").replace("\\", "/")).name
                filename = "".join(c for c in filename if ord(c) >= 32)[:160] or "package-image"
                record["images"].append({"filename": filename, "mime": mime, "sha256": hashes[str(original)],
                                         "bytes": original.stat().st_size, "original_size": list(capture["original_size"]),
                                         "panel": capture["panel"], "rotation": capture["rotation_clockwise"], "crop": capture["crop"]})
                # receive validated the edited view. Drafts retain only originals;
                # previews are regenerated on resume, final frames on Analyze.
                frame = Path(capture["frame"])
                if frame.resolve().parent == original.parent.resolve():
                    frame.unlink()
            total = sum(item["bytes"] for item in record["images"])
            if total > MAX_DRAFT_BYTES:
                raise DraftError("Draft originals exceed the 100 MB limit.", 413)
            with self.repo._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                prior_count = self._parent(conn, details, user)
                fingerprint = hashlib.sha256(json.dumps({"details": details, "images": record["images"]},
                                                        sort_keys=True, allow_nan=False).encode()).hexdigest()
                if prior_count + len(files) > 12:
                    raise DraftError("A linked draft can have at most 12 retained and new images combined.")
                old = conn.execute("SELECT * FROM capture_draft WHERE id=?", (draft_id,)).fetchone()
                if old is not None:
                    old = self._row(conn, draft_id, user)
                    if old["save_token"] == save_token:
                        if old["fingerprint"] != fingerprint:
                            raise DraftError("This save attempt changed. Start a new save attempt.", 409)
                        return self._public(old)
                    if old["revision"] != revision:
                        raise DraftError("This draft was updated in another tab. Resume its latest version before saving.", 409)
                    old_session = json.loads(old["record"])["session"]
                elif revision:
                    raise DraftError("This capture draft no longer exists.", 404)
                quota = conn.execute("SELECT COUNT(*), COALESCE(SUM(total_bytes),0) FROM capture_draft WHERE owner_id=? AND state='active' AND expires_at>? AND id<>?",
                                     (user.id, self.clock(), draft_id)).fetchone()
                if quota[0] >= MAX_DRAFTS or quota[1] + total > MAX_ACCOUNT_BYTES:
                    raise DraftError("Saved drafts are limited to 10 and 300 MB per account. Delete an older draft first.", 413)
                now = self.clock()
                conn.execute("""INSERT INTO capture_draft VALUES (?,?,?,'active',?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, updated_at=excluded.updated_at,
                    expires_at=excluded.expires_at,total_bytes=excluded.total_bytes,save_token=excluded.save_token,
                    fingerprint=excluded.fingerprint,record=excluded.record""",
                             (draft_id, user.id, revision + 1, old["created_at"] if old else now, now,
                              now + RETENTION_SECONDS, total, save_token, fingerprint, json.dumps(record, allow_nan=False)))
                self._audit(conn, user.id, "capture_draft.updated" if old else "capture_draft.created", draft_id,
                            before={"revision": revision} if old else None,
                            after={"revision": revision + 1, "image_count": len(files), "total_bytes": total})
                result = self._public(conn.execute("SELECT * FROM capture_draft WHERE id=?", (draft_id,)).fetchone())
            committed = True
            return result
        finally:
            self._remove_session(old_session if committed else session)

    def delete(self, draft_id, revision, user):
        _identifier(draft_id)
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._row(conn, draft_id, user)
            if row["revision"] != revision:
                raise DraftError("This draft was updated in another tab. Refresh the saved list before deleting.", 409)
            conn.execute("UPDATE capture_draft SET state='deleted' WHERE id=?", (draft_id,))
            self._audit(conn, user.id, "capture_draft.deleted", draft_id, before={"revision": revision})
            session = json.loads(row["record"])["session"]
        self._remove_session(session)

    def _referenced(self, conn, session):
        target = str((self.root / session).resolve()).replace("\\", "/").casefold().rstrip("/")

        def contains(value):
            if isinstance(value, str):
                normalized = value.replace("\\", "/").casefold()
                return normalized == target or normalized.startswith(target + "/")
            if isinstance(value, dict):
                return any(contains(key) or contains(item) for key, item in value.items())
            return isinstance(value, list) and any(contains(item) for item in value)

        for row in conn.execute("SELECT record FROM capture_draft WHERE state='active'"):
            if json.loads(row[0]).get("session") == session:
                return True
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in (("inspection", "record"), ("inspection_revision", "record"), ("inspection_job", "payload")):
            if table in tables:
                for row in conn.execute(f"SELECT {column} FROM {table}"):
                    if contains(json.loads(row[0])):
                        return True
        return False

    def _remove_session(self, session):
        if not session or not _ID.fullmatch(session):
            return
        target = self.root / session
        # Never follow a replacement symlink or traverse out of owned storage.
        if target.is_symlink() or target.resolve().parent != self.root.resolve():
            return
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._referenced(conn, session) and target.is_dir():
                try:
                    shutil.rmtree(target)
                except OSError:
                    pass  # An interrupted/open-file cleanup is retried later.

    def cleanup(self):
        with self.repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT id,revision FROM capture_draft WHERE state='active' AND expires_at<=?", (self.clock(),)).fetchall():
                conn.execute("UPDATE capture_draft SET state='expired' WHERE id=?", (row["id"],))
                self._audit(conn, None, "capture_draft.expired", row["id"], before={"revision": row["revision"]})
            sessions = [json.loads(row[0])["session"] for row in conn.execute("SELECT record FROM capture_draft WHERE state IN ('deleted','expired')")]
        for session in sessions:
            self._remove_session(session)
        # Crash remnants have no DB record. Leave recent in-flight saves alone.
        for path in self.root.iterdir():
            try:
                old = _ID.fullmatch(path.name) and path.is_dir() and path.stat().st_mtime < self.clock() - 86400
            except OSError:
                continue  # Another worker may have removed an orphan already.
            if old:
                self._remove_session(path.name)
