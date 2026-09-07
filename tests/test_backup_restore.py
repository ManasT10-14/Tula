"""Real isolated runtime recovery, malicious archives and maintenance exclusion."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file
from tula.report.evidence import verify
from tula.rules.engine import RulesEngine
from tula.rules.spec import Rule, RulePack
from tula.security import SecurityStore
from tula.storage import backup
from tula.storage.db import Repository
from tula.web import lifecycle
from tula.web.lifecycle import install_job_lifecycle


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "runtime"
    uploads = root / "data/uploads"
    uploads.mkdir(parents=True)
    original, frame = uploads / "original.png", uploads / "working.png"
    Image.new("RGB", (100, 80), "white").save(original)
    frame.write_bytes(original.read_bytes())
    repo = Repository(root / "data/tula.db")
    pack = RulePack(version="backup-immutable-v1", title="Retained test rules", rules=[
        Rule(id="BACKUP.TEST", title="Backup fixture", citation={"clause": "Test only"},
             effective_from=date(2020, 1, 1))])
    repo.archive_rules(pack)
    a = Analysis(scan=Scan(scan_id="BACKUP-SCAN", inspector_id="original-owner", frames=[str(frame)],
                 frame_hashes={str(frame): sha256_file(frame)},
                 original_frame_hashes={str(original): sha256_file(original)}),
                 package=PackageFacts(), rules_version=pack.version)
    repo.save(a)
    repo.revise(a.scan.scan_id, 0, "reviewer", "inspection.comment", "Retain the original evidence.",
                lambda a: a.review.comments.append({"actor_id": "reviewer", "text": "Historical comment"}))
    security = SecurityStore(repo.path)
    security.create_user("backup.admin", "A temporary backup test passphrase!", role="admin", bootstrap=True)
    report = root / "out/retained-notice.docx"
    report.parent.mkdir()
    report.write_bytes(b"Exact retained report artifact bytes")
    with repo._connect() as conn:
        conn.execute("INSERT INTO report_artifact VALUES (?,?,?,?,?,?,?,?)",
                     ("report", a.scan.scan_id, 1, "notice.docx", str(report), sha256_file(report),
                      "reviewer", "2026-09-07T00:00:00Z"))
        preserved = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                     for table in ("inspection", "inspection_revision", "security_user", "security_audit",
                                   "rule_version", "report_artifact", "compliance_finding")}
    rules = root / "rules/old-version.json"
    rules.parent.mkdir()
    rules.write_bytes(b'{ "retained" : "rule file bytes" }\n')
    return SimpleNamespace(root=root, repo=repo, original=original, frame=frame, report=report,
                           preserved=preserved, archive=tmp_path / "safe-backup.zip", pack=pack)


def retire(case):
    destination = case.root.with_name("original-runtime-retained")
    assert case.root.resolve().is_relative_to(case.archive.parent.resolve())
    assert destination.resolve().is_relative_to(case.archive.parent.resolve())
    case.root.rename(destination)
    return destination


def rewrite_archive(source, destination, change):
    with zipfile.ZipFile(source) as archive:
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    change(entries)
    with zipfile.ZipFile(destination, "w") as archive:
        for name, (info, content) in entries.items():
            archive.writestr(info if info.filename == name else name, content)


def test_consistent_backup_and_restore_preserve_retained_bytes_and_users(case):
    created = backup.create_backup(case.root, case.archive)
    assert created["verification"] == {"inspection_records": 3, "verified_reference_files": 3,
                                        "archived_rule_versions": 1, "active_capture_drafts": 0,
                                        "draft_original_files": 0}
    verified = backup.verify_backup(case.archive)
    assert verified["runtime_root"] == str(case.root)
    with zipfile.ZipFile(case.archive) as archive:
        assert "data/tula.db-wal" not in archive.namelist()
        assert "data/tula.db-shm" not in archive.namelist()
    old = retire(case)
    backup.restore_backup(case.archive, case.root)
    with closing(sqlite3.connect(case.repo.path)) as conn:
        for table, rows in case.preserved.items():
            assert list(conn.execute(f"SELECT * FROM {table}")) == rows
    restored = Repository(case.repo.path)
    assert all(row["status"] == "verified" for row in verify(restored.get("BACKUP-SCAN")))
    assert case.frame.read_bytes() == (old / "data/uploads/working.png").read_bytes()
    assert case.report.read_bytes() == (old / "out/retained-notice.docx").read_bytes()
    assert (case.root / "rules/old-version.json").read_bytes() == (old / "rules/old-version.json").read_bytes()
    assert SecurityStore(case.repo.path).list_users()[0].username == "backup.admin"


@pytest.mark.parametrize("damage", ["missing_original", "changed_original", "missing_report"])
def test_backup_rejects_missing_or_corrupt_retained_files(case, damage):
    if damage == "missing_original":
        case.original.unlink()
    elif damage == "missing_report":
        case.report.unlink()
    else:
        case.original.write_bytes(b"Unrecorded evidence changes")
    with pytest.raises(ValueError, match="missing|hash check"):
        backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()


def test_backup_refuses_references_outside_runtime(case):
    path = case.root.parent / "outside-evidence.png"
    path.write_bytes(case.frame.read_bytes())
    with case.repo._connect() as conn:
        raw = json.loads(conn.execute("SELECT record FROM inspection").fetchone()[0])
        raw["scan"]["frames"] = [str(path)]
        raw["scan"]["frame_hashes"] = {str(path): sha256_file(path)}
        conn.execute("UPDATE inspection SET record=?", (json.dumps(raw),))
    with pytest.raises(ValueError, match="outside the runtime"):
        backup.create_backup(case.root, case.archive)


def test_backup_refuses_existing_output_and_output_inside_runtime(case):
    case.archive.write_bytes(b"Keep this archive")
    with pytest.raises(ValueError, match="new archive"):
        backup.create_backup(case.root, case.archive)
    assert case.archive.read_bytes() == b"Keep this archive"
    with pytest.raises(ValueError, match="outside"):
        backup.create_backup(case.root, case.root / "out/archive.zip")


def test_restore_refuses_relocation_and_existing_destination(case):
    backup.create_backup(case.root, case.archive)
    with pytest.raises(ValueError, match="absent destination"):
        backup.restore_backup(case.archive, case.root)
    before = case.frame.read_bytes()
    with pytest.raises(ValueError, match="original absolute runtime"):
        backup.restore_backup(case.archive, case.root.with_name("relocated"))
    assert case.frame.read_bytes() == before
    assert not case.root.with_name("relocated").exists()


@pytest.mark.parametrize("damage", ["changed_member", "missing_member", "missing_manifest", "unexpected_member"])
def test_corrupt_archive_never_publishes_restore(case, damage):
    backup.create_backup(case.root, case.archive)
    corrupt = case.archive.with_name("corrupt.zip")

    def change(entries):
        if damage == "changed_member":
            info, payload = entries["data/uploads/original.png"]
            entries[info.filename] = (info, bytes([payload[0] ^ 1]) + payload[1:])
        elif damage == "missing_member":
            entries.pop("data/uploads/original.png")
        elif damage == "missing_manifest":
            entries.pop("manifest.json")
        else:
            entries["data/unlisted"] = (zipfile.ZipInfo("data/unlisted"), b"Unlisted")
    rewrite_archive(case.archive, corrupt, change)
    retire(case)
    with pytest.raises(ValueError, match="manifest|entries"):
        backup.restore_backup(corrupt, case.root)
    assert not case.root.exists()
    assert not list(case.root.parent.glob(".tula-restore-*"))


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", "data/../../escape",
                                  "data\\escape", "data/CON", "data/foo.", "data/tula.db-wal"])
def test_zip_slip_and_windows_aliases_are_rejected(case, name):
    backup.create_backup(case.root, case.archive)
    corrupt = case.archive.with_name("unsafe.zip")
    def change(entries):
        info = zipfile.ZipInfo()
        info.filename = name  # Avoid ZipInfo's Windows constructor normalization.
        entries[name] = (info, b"escaped")
    rewrite_archive(case.archive, corrupt, change)
    with pytest.raises(ValueError, match="Unsafe|sidecars"):
        backup.verify_backup(corrupt)
    assert not (case.root.parent / "escape").exists()


def test_zip_symlink_is_rejected(case):
    backup.create_backup(case.root, case.archive)
    corrupt = case.archive.with_name("link.zip")

    def change(entries):
        info = zipfile.ZipInfo("data/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries[info.filename] = (info, b"../../outside")
    rewrite_archive(case.archive, corrupt, change)
    with pytest.raises(ValueError, match="without links"):
        backup.verify_backup(corrupt)


def test_hardlinked_source_is_rejected(case):
    os.link(case.frame, case.root / "data/uploads/hardlink.png")
    with pytest.raises(ValueError, match="hard links"):
        backup.create_backup(case.root, case.archive)


def test_active_sqlite_writer_is_rejected_without_waiting(case):
    with closing(sqlite3.connect(case.repo.path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="active writer"):
            backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()


def test_shared_runtime_leases_allow_workers_but_exclude_maintenance(case):
    with (backup.runtime_lease(case.root), backup.runtime_lease(case.root),
          pytest.raises(ValueError, match="Runtime is active")):
        backup.create_backup(case.root, case.archive)
    backup.create_backup(case.root, case.archive)


def test_asgi_lifespan_holds_lease_and_releases_after_shutdown(case):
    app = FastAPI()
    web = SimpleNamespace(app=app, ROOT=case.root, repo=case.repo, rules=RulesEngine(case.pack))
    install_job_lifecycle(web)
    with TestClient(app), pytest.raises(ValueError, match="Runtime is active"):
        backup.create_backup(case.root, case.archive)
    backup.create_backup(case.root, case.archive)


def test_exclusive_maintenance_lease_prevents_server_start(case):
    app = FastAPI()
    web = SimpleNamespace(app=app, ROOT=case.root, repo=case.repo, rules=RulesEngine(case.pack))
    install_job_lifecycle(web)
    with (backup.runtime_lease(case.root, exclusive=True),
          pytest.raises(ValueError, match="maintenance"), TestClient(app)):
        pytest.fail("Server must not start during maintenance")


def test_detects_noncooperating_file_change_during_copy(case, monkeypatch):
    copy = backup.shutil.copyfile

    def changed(source, destination):
        result = copy(source, destination)
        if Path(source) == case.frame:
            case.frame.write_bytes(b"Concurrent unrecorded file write")
        return result
    monkeypatch.setattr(backup.shutil, "copyfile", changed)
    with pytest.raises(ValueError, match="changed during backup"):
        backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()


def test_manifest_hash_does_not_bypass_retained_evidence_hash(case):
    backup.create_backup(case.root, case.archive)
    corrupt = case.archive.with_name("false-hash.zip")

    def change(entries):
        name = "data/uploads/original.png"
        info, content = entries[name]
        content = bytes([content[0] ^ 1]) + content[1:]
        entries[name] = (info, content)
        info, raw = entries["manifest.json"]
        manifest = json.loads(raw)
        manifest["files"][name]["sha256"] = hashlib.sha256(content).hexdigest()
        entries["manifest.json"] = (info, json.dumps(manifest).encode())
    rewrite_archive(case.archive, corrupt, change)
    with pytest.raises(ValueError, match="retained evidence.*hash check"):
        backup.verify_backup(corrupt)


def test_cli_verifies_without_printing_account_or_evidence_content(case, capsys):
    assert backup.main(["create", str(case.archive), "--root", str(case.root)]) == 0
    assert backup.main(["verify", str(case.archive)]) == 0
    output = capsys.readouterr().out
    assert "retained inspection records verified" in output
    assert "backup.admin" not in output and "passphrase" not in output


def test_committed_wal_pages_are_included_in_database_snapshot(case):
    with closing(sqlite3.connect(case.repo.path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE wal_recovery_test (value TEXT)")
        writer.execute("INSERT INTO wal_recovery_test VALUES ('committed in WAL')")
        writer.commit()
        assert Path(str(case.repo.path) + "-wal").stat().st_size > 0
        backup.create_backup(case.root, case.archive)
    extracted = case.root.parent / "verified-snapshot.db"
    with zipfile.ZipFile(case.archive) as archive:
        extracted.write_bytes(archive.read(backup.DATABASE))
    with closing(sqlite3.connect(extracted)) as conn:
        assert conn.execute("SELECT value FROM wal_recovery_test").fetchone()[0] == "committed in WAL"


@pytest.mark.parametrize("state", ["queued", "running"])
def test_queued_and_running_jobs_block_offline_backup(case, state):
    with case.repo._connect() as conn:
        conn.execute("CREATE TABLE inspection_job (state TEXT, payload TEXT)")
        conn.execute("INSERT INTO inspection_job VALUES (?, '{}')", (state,))
    with pytest.raises(ValueError, match="Queued or running"):
        backup.create_backup(case.root, case.archive)


def test_case_colliding_archive_members_are_rejected(case):
    backup.create_backup(case.root, case.archive)
    corrupt = case.archive.with_name("collision.zip")
    name = "DATA/uploads/original.png"
    rewrite_archive(case.archive, corrupt, lambda entries:
                    entries.update({name: (zipfile.ZipInfo(name), b"collision")}))
    with pytest.raises(ValueError, match="duplicate names"):
        backup.verify_backup(corrupt)


def test_restore_publish_never_replaces_a_destination_created_after_verification(case, monkeypatch):
    backup.create_backup(case.root, case.archive)
    retire(case)
    publish = backup._publish_directory

    def create_first(stage, destination):
        destination.mkdir()
        publish(stage, destination)
    monkeypatch.setattr(backup, "_publish_directory", create_first)
    with pytest.raises((ValueError, OSError)):
        backup.restore_backup(case.archive, case.root)
    assert case.root.is_dir() and list(case.root.iterdir()) == []


def test_source_symlink_is_rejected_without_reading_target(case):
    link = case.root / "data/uploads/link.png"
    try:
        link.symlink_to(case.frame)
    except OSError:
        pytest.skip("Creating symlinks requires additional Windows privileges")
    with pytest.raises(ValueError, match="links or junctions"):
        backup.create_backup(case.root, case.archive)


def test_shutdown_timeout_keeps_maintenance_excluded_until_worker_exits(case, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class SlowWorker:
        def stop(self, timeout=2):
            if timeout is not None:
                return False
            entered.set()
            assert release.wait(5)
            return True

    app = FastAPI()
    web = SimpleNamespace(app=app, ROOT=case.root, repo=case.repo, rules=RulesEngine(case.pack))
    monkeypatch.setattr(lifecycle, "get_jobs", lambda web: SlowWorker())
    install_job_lifecycle(web)
    with TestClient(app):
        pass
    try:
        assert entered.wait(2)
        with pytest.raises(ValueError, match="Runtime is active"):
            backup.create_backup(case.root, case.archive)
    finally:
        release.set()
        app.state.shutdown_lease_thread.join(3)
    assert not app.state.shutdown_lease_thread.is_alive()
    backup.create_backup(case.root, case.archive)


def test_restore_refuses_a_live_runtime_lease_even_after_directory_was_moved(case):
    backup.create_backup(case.root, case.archive)
    with backup.runtime_lease(case.root):
        retire(case)
        with pytest.raises(ValueError, match="Runtime is active"):
            backup.restore_backup(case.archive, case.root)
    assert not case.root.exists()


@pytest.mark.parametrize("damage", ["invalid_database", "changed_rule_archive", "missing_rule_archive"])
def test_database_and_retained_rule_archive_integrity_are_required(case, damage):
    if damage == "invalid_database":
        case.repo.path.write_bytes(b"Corrupt SQLite bytes")
    else:
        with case.repo._connect() as conn:
            if damage == "changed_rule_archive":
                conn.execute("UPDATE rule_version SET sha256=?", ("0" * 64,))
            else:
                # Simulates an old DB record whose original archive was never retained.
                raw = json.loads(conn.execute("SELECT record FROM inspection").fetchone()[0])
                raw["rules_version"] = "missing-historical-version"
                conn.execute("UPDATE inspection SET record=?", (json.dumps(raw),))
    with pytest.raises((ValueError, sqlite3.Error)):
        backup.create_backup(case.root, case.archive)
    assert not case.archive.exists()


def test_oversized_declared_archive_is_rejected_before_extraction(case, monkeypatch):
    backup.create_backup(case.root, case.archive)
    monkeypatch.setattr(backup, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="uncompressed size"):
        backup.verify_backup(case.archive)
