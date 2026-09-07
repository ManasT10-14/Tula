"""Capture drafts remain private, byte-exact and resumable after offline recovery."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from PIL import Image

from tula.security import SecurityStore
from tula.services.capture_drafts import CaptureDrafts, DraftError
from tula.storage import backup
from tula.storage.db import Repository


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "runtime"
    repo = Repository(root / "data/tula.db")
    security = SecurityStore(repo.path)
    user = security.create_user("draft.backup", "A temporary draft backup passphrase!", role="admin", bootstrap=True)
    service = CaptureDrafts(repo, root, security)
    originals = []
    for color in ("white", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (120, 80), color).save(buffer, format="PNG")
        originals.append(buffer.getvalue())
    draft_id = uuid.uuid4().hex
    details = {"title": "Retained unfinished package", "lane": "citizen", "region": "Test district",
               "concerns": "milk, soy", "complete": True, "dimensions": {"pdp_width_mm": 90.5},
               "context": {"category": "food", "category_confirmed": True, "bundle_type": "single",
                           "bundle_confirmed": True, "shape": "rectangular", "shape_confirmed": True,
                           "is_imported": False, "imported_confirmed": True}}
    files = [UploadFile(io.BytesIO(data), filename=f"package-{index}.png", size=len(data))
             for index, data in enumerate(originals)]
    service.save(draft_id, 0, uuid.uuid4().hex, user, files, details=details, panels="back,left",
                 edits=json.dumps([{"rotation": 90, "crop": [.1, .1, .9, .9]}, {}]))
    with repo._connect() as conn:
        retained_row = tuple(conn.execute("SELECT * FROM capture_draft").fetchone())
        record = json.loads(conn.execute("SELECT record FROM capture_draft").fetchone()[0])
    yield SimpleNamespace(root=root, repo=repo, service=service, security=security, user=user,
                          originals=originals, id=draft_id, record=record, retained_row=retained_row,
                          archive=tmp_path / "draft-backup.zip")
    for upload in files:
        upload.file.close()


def original(case, index=0):
    return case.root / "data/capture-drafts" / case.record["session"] / f"{index:02d}-original.bin"


def retire(case):
    retained = case.root.with_name("retained-original-runtime")
    assert case.root.resolve().is_relative_to(case.archive.parent.resolve())
    assert retained.resolve().is_relative_to(case.archive.parent.resolve())
    case.root.rename(retained)
    return retained


def rewrite(case, *, member_change=None, database_change=None, manifest_change=None):
    """Recompute the outer manifest: semantic DB checks must still catch damage."""
    destination = case.archive.with_name("modified.zip")
    with zipfile.ZipFile(case.archive) as source:
        members = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(members.pop("manifest.json"))
    if member_change:
        member_change(members)
    if database_change:
        database = case.archive.with_name("modified.db")
        database.write_bytes(members[backup.DATABASE])
        with closing(sqlite3.connect(database)) as conn:
            database_change(conn)
            conn.commit()
        members[backup.DATABASE] = database.read_bytes()
    manifest["files"] = {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                         for name, data in members.items()}
    if manifest_change:
        manifest_change(manifest)
    with zipfile.ZipFile(destination, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            output.writestr(name, data)
    return destination


def test_real_draft_roundtrip_preserves_originals_edits_rows_and_owner(case):
    before = case.service.get(case.id, case.user)
    manifest = backup.create_backup(case.root, case.archive)
    assert manifest["verification"]["active_capture_drafts"] == 1
    assert manifest["verification"]["draft_original_files"] == 2
    assert manifest["verification"]["verified_reference_files"] == 2
    assert backup.verify_backup(case.archive)["verification"] == manifest["verification"]
    old = retire(case)
    backup.restore_backup(case.archive, case.root)
    repo = Repository(case.repo.path)
    restored = CaptureDrafts(repo, case.root, SecurityStore(repo.path))
    with repo._connect() as conn:
        assert tuple(conn.execute("SELECT * FROM capture_draft").fetchone()) == case.retained_row
        assert conn.execute("SELECT COUNT(*) FROM inspection").fetchone()[0] == 0
    after = restored.get(case.id, case.user)
    assert after == before
    assert after["images"][0]["rotation"] == 90
    assert after["images"][0]["crop"] == [.1, .1, .9, .9]
    assert after["images"][1]["panel"] == "left"
    assert after["details"]["dimensions"] == {"pdp_width_mm": 90.5}
    assert after["details"]["complete"] is False
    assert all(value is False for name, value in after["details"]["context"].items() if name.endswith("_confirmed"))
    for index, data in enumerate(case.originals):
        assert restored.image(case.id, index, 1, case.user)[0] == data
        assert original(case, index).read_bytes() == (old / original(case, index).relative_to(case.root)).read_bytes()
    with pytest.raises(DraftError, match="unavailable"):
        restored.get(case.id, SimpleNamespace(id="other-administrator", role="admin"))


@pytest.mark.parametrize("damage", ["missing", "changed", "wrong_size"])
def test_backup_refuses_damaged_active_draft_original(case, damage):
    path = original(case)
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    else:
        path.write_bytes(path.read_bytes() + b"additional bytes")
    with pytest.raises(ValueError, match="draft original.*missing|draft original.*hash or size"):
        backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "size", "swapped_indices"])
def test_verify_and_restore_check_inner_draft_manifest_despite_rehashed_zip(case, damage):
    backup.create_backup(case.root, case.archive)
    first = original(case).relative_to(case.root).as_posix()
    second = original(case, 1).relative_to(case.root).as_posix()

    def change(members):
        if damage == "missing":
            members.pop(first)
        elif damage == "changed":
            data = members[first]
            members[first] = bytes([data[0] ^ 1]) + data[1:]
        elif damage == "size":
            members[first] += b"extra bytes"
        else:
            members[first], members[second] = members[second], members[first]
    modified = rewrite(case, member_change=change)
    retire(case)
    for operation in (lambda: backup.verify_backup(modified), lambda: backup.restore_backup(modified, case.root)):
        with pytest.raises(ValueError, match="draft original.*missing|draft original.*hash or size"):
            operation()
    assert not case.root.exists()
    assert not list(case.root.parent.glob(".tula-restore-*"))


@pytest.mark.parametrize("damage", ["session_traversal", "session_absolute", "session_alias", "missing_images",
                                  "too_many_images", "invalid_hash", "negative_bytes", "boolean_bytes",
                                  "total_mismatch", "invalid_dimensions", "invalid_rotation", "invalid_crop",
                                  "tiny_crop", "fingerprint"])
def test_active_draft_manifest_structure_is_validated_without_rewriting(case, damage):
    with case.repo._connect() as conn:
        record = json.loads(conn.execute("SELECT record FROM capture_draft").fetchone()[0])
        total = sum(item["bytes"] for item in record["images"])
        if damage.startswith("session_"):
            record["session"] = {"session_traversal": "../uploads", "session_absolute": "C:/outside",
                                 "session_alias": "A" * 32}[damage]
        elif damage == "missing_images":
            record["images"] = []
        elif damage == "too_many_images":
            record["images"] *= 7
        elif damage == "invalid_hash":
            record["images"][0]["sha256"] = "not-a-hash"
        elif damage in {"negative_bytes", "boolean_bytes"}:
            record["images"][0]["bytes"] = -1 if damage == "negative_bytes" else True
        elif damage == "total_mismatch":
            total += 1
        elif damage == "invalid_dimensions":
            record["images"][0]["original_size"] = [True, 80]
        elif damage == "invalid_rotation":
            record["images"][0]["rotation"] = 45
        elif damage in {"invalid_crop", "tiny_crop"}:
            record["images"][0]["crop"] = [0, 0, 2, 1] if damage == "invalid_crop" else [0, 0, .01, .01]
        fingerprint = hashlib.sha256(json.dumps({"details": record["details"], "images": record["images"]},
                                                sort_keys=True).encode()).hexdigest()
        if damage == "fingerprint":
            fingerprint = "0" * 64
        conn.execute("UPDATE capture_draft SET record=?,fingerprint=?,total_bytes=?",
                     (json.dumps(record), fingerprint, total))
        retained = conn.execute("SELECT record FROM capture_draft").fetchone()[0]
    with pytest.raises(ValueError, match="draft image manifest"):
        backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()
    with case.repo._connect() as conn:
        assert conn.execute("SELECT record FROM capture_draft").fetchone()[0] == retained


@pytest.mark.parametrize("state", ["deleted", "expired", "active_past_expiry"])
def test_deleted_and_expired_draft_metadata_does_not_require_cleaned_originals(case, state):
    if state == "deleted":
        case.service.delete(case.id, 1, case.user)
    elif state == "expired":
        case.service.clock = lambda: 9_999_999_999
        case.service.cleanup()
    else:
        with case.repo._connect() as conn:
            conn.execute("UPDATE capture_draft SET expires_at=0")
        for index in range(2):
            original(case, index).unlink()
    assert not original(case).exists()
    with case.repo._connect() as conn:
        before = tuple(conn.execute("SELECT * FROM capture_draft").fetchone())
    created = backup.create_backup(case.root, case.archive)
    assert created["verification"]["active_capture_drafts"] == 0
    retire(case)
    backup.restore_backup(case.archive, case.root)
    with closing(sqlite3.connect(case.repo.path)) as conn:
        assert tuple(conn.execute("SELECT * FROM capture_draft").fetchone()) == before


def test_later_expiry_never_weakens_an_existing_archives_draft_checks(case, monkeypatch):
    with case.repo._connect() as conn:
        conn.execute("UPDATE capture_draft SET expires_at=?", (datetime(2020, 2, 1, tzinfo=UTC).timestamp(),))

    class SnapshotClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2020, 1, 1, tzinfo=UTC)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "datetime", SnapshotClock)
        manifest = backup.create_backup(case.root, case.archive)
    assert manifest["verification"]["active_capture_drafts"] == 1
    assert backup.verify_backup(case.archive)["verification"]["active_capture_drafts"] == 1
    member = original(case).relative_to(case.root).as_posix()
    damaged = rewrite(case, member_change=lambda members: members.pop(member))
    with pytest.raises(ValueError, match="draft original.*missing"):
        backup.verify_backup(damaged)


@pytest.mark.parametrize("created_at", [None, "2026-09-07", "not-a-date"])
def test_archive_requires_a_timezone_aware_snapshot_time(case, created_at):
    backup.create_backup(case.root, case.archive)
    damaged = rewrite(case, manifest_change=lambda manifest: manifest.update(created_at=created_at))
    with pytest.raises(ValueError, match="Archive creation time"):
        backup.verify_backup(damaged)
