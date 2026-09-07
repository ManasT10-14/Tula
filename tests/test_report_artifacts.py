"""Real export bytes, retained revisions and atomic audit/artifact records."""
import json
import logging
import sqlite3
from types import SimpleNamespace

import pytest
from PIL import Image

from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file
from tula.security import SecurityStore
from tula.services.reports import ReportError, export_inspection
from tula.storage.db import Repository


@pytest.fixture
def case(tmp_path):
    repo = Repository(tmp_path / "data" / "tula.db")
    security = SecurityStore(repo.path)
    actor = security.create_user("qa.report", "Report accountability test 2026!", role="admin", bootstrap=True)
    frame = tmp_path / "evidence.png"
    Image.new("RGB", (200, 100), "white").save(frame)
    a = Analysis(package=PackageFacts(), scan=Scan(scan_id="EXPORT-QA", frames=[str(frame)],
                           frame_hashes={str(frame): sha256_file(str(frame))}, source="bench"))
    repo.save(a)
    return SimpleNamespace(repo=repo, security=security, actor=actor, a=a, out=tmp_path / "reports", frame=frame)


def generate(case, fmt="pdf"):
    return export_inspection(case.repo, case.security, case.actor, case.a, case.out, fmt)


def artifacts(case):
    with case.repo._connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM report_artifact")]


@pytest.mark.parametrize("fmt", ["pdf", "docx", "notice.docx"])
def test_every_export_retains_actor_revision_bytes_and_matching_audit(case, fmt):
    original = case.frame.read_bytes()
    path = generate(case, fmt)
    row, = artifacts(case)
    assert row["path"] == str(path) and row["format"] == fmt
    assert row["actor_id"] == case.actor.id and row["revision"] == 0
    assert row["sha256"] == sha256_file(str(path))
    event, = case.security.audit_events(entity_id="EXPORT-QA")
    assert event["action"] == "report.generated"
    assert event["after"] == {"artifact_id": row["id"], "format": fmt,
                                              "revision": 0, "sha256": row["sha256"]}
    assert case.frame.read_bytes() == original
    assert path.read_bytes().startswith(b"%PDF" if fmt == "pdf" else b"PK")


def test_audit_failure_rolls_back_artifact_and_removes_only_new_file(case, monkeypatch):
    retained = generate(case)
    original = retained.read_bytes()

    def failed_audit(*args, **kwargs):
        raise sqlite3.OperationalError("Injected audit write failure")

    monkeypatch.setattr(case.security, "_audit", failed_audit)
    with pytest.raises(sqlite3.OperationalError):
        generate(case, "notice.docx")
    assert len(artifacts(case)) == 1
    assert list(case.out.iterdir()) == [retained] and retained.read_bytes() == original


@pytest.mark.parametrize("problem", ["changed", "missing", "no_frames"])
def test_damaged_or_absent_evidence_cannot_create_artifact(case, problem):
    if problem == "changed":
        case.frame.write_bytes(b"altered")
    elif problem == "missing":
        case.frame.unlink()
    else:
        case.a.scan.frames = []
    with pytest.raises(ReportError, match="Evidence"):
        generate(case)
    assert not artifacts(case) and not case.out.exists()


def test_evidence_change_during_render_is_rejected(case, monkeypatch):
    from tula.services import reports
    real_write = reports.pdf.write

    def changed_during_render(a, path, **kwargs):
        real_write(a, path, **kwargs)
        case.frame.write_bytes(b"changed after render")

    monkeypatch.setattr(reports.pdf, "write", changed_during_render)
    with pytest.raises(ReportError):
        generate(case)
    assert not artifacts(case) and list(case.out.iterdir()) == []


def test_unknown_format_does_not_write_anything(case):
    with pytest.raises(ValueError, match="format"):
        generate(case, "html")
    assert not case.out.exists()


def test_report_operational_events_are_correlated_without_paths_or_notice_text(case, caplog):
    secret = "PRIVATE PREMISES VALUE"
    with caplog.at_level(logging.INFO, logger="tula.reports"):
        path = export_inspection(
            case.repo, case.security, case.actor, case.a, case.out, "notice.docx",
            premises=secret,
        )
    events = [record for record in caplog.records if record.name == "tula.reports"]
    assert [record.event for record in events] == [
        "report_generation_started", "report_generation_completed"
    ]
    completed = json.loads(events[-1].getMessage())
    assert completed["scan_id"] == "EXPORT-QA"
    assert completed["format"] == "notice.docx" and completed["revision"] == 0
    assert completed["duration_ms"] >= 0 and completed["artifact_id"]
    rendered = "\n".join(record.getMessage() for record in events)
    assert secret not in rendered and str(path) not in rendered and str(case.frame) not in rendered
