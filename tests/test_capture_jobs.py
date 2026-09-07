"""Capture evidence and durable queue contracts, separate from OCR accuracy.

Queue tests replace only expensive analysis with an explicitly named test stub;
file transforms, hashes, SQLite state, worker threads, sessions and HTTP routes
are real. Real model/image verification lives in benchmark_difficult_labels.py.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageDraw

from tula.domain.enums import Lane, Panel, Verdict
from tula.domain.models import Analysis, Citation, Finding, PackageFacts, Scan, sha256_file
from tula.rules.engine import RulesEngine
from tula.security import SecurityStore, User, install_security
from tula.security.web import LOGIN_CSRF_COOKIE
from tula.services import jobs as jobs_module
from tula.services.capture import CaptureError, receive
from tula.services.jobs import InspectionJobs
from tula.storage.db import Repository
from tula.web.workflow import install_workflow

OWNER = User("capture-owner", "capture-owner", "Capture Inspector", "inspector", True)
PASSWORD = "Capture queue test passphrase 2026!"


def picture(size=(80, 60), *, fmt="PNG", exif=None):
    image = Image.new("RGB", size, "red")
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 2, 0, size[0], size[1] // 2), fill="green")
    draw.rectangle((0, size[1] // 2, size[0] // 2, size[1]), fill="blue")
    draw.rectangle((size[0] // 2, size[1] // 2, size[0], size[1]), fill="yellow")
    payload = io.BytesIO()
    image.save(payload, format=fmt, **({"exif": exif} if exif is not None else {}))
    return payload.getvalue()


def upload(data=None, filename="package.png"):
    return UploadFile(file=io.BytesIO(picture() if data is None else data), filename=filename)


def test_rotate_then_crop_preserves_original_bytes_hash_and_pixels(tmp_path):
    data = picture()
    captures, hashes, records = receive([upload(data, "../../outside.png")], tmp_path,
                                        edits='[{"rotation":90,"crop":[0,0,0.5,1]}]')
    record = records[0]
    original = Path(record["original"])
    assert original.read_bytes() == data
    assert hashes[str(original)] == hashlib.sha256(data).hexdigest()
    assert Path(captures[0].path).resolve().is_relative_to(tmp_path.resolve())
    assert not (tmp_path.parent / "outside.png").exists()
    assert record["rotation_clockwise"] == 90 and record["crop"] == [0, 0, .5, 1]
    assert record["original_size"] == (80, 60)
    with Image.open(io.BytesIO(data)) as source, Image.open(captures[0].path) as actual:
        expected = source.rotate(-90, expand=True).crop((0, 0, 30, 80))
        assert actual.size == (30, 80)
        assert ImageChops.difference(actual, expected).getbbox() is None


def test_exif_orientation_normalized_without_reencoding_original(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    data = picture((100, 60), fmt="JPEG", exif=exif)
    captures, hashes, records = receive([upload(data, "camera.heic")], tmp_path)
    with Image.open(captures[0].path) as image:
        assert image.size == (60, 100)
        assert image.getexif().get(274) is None
    assert Path(records[0]["original"]).read_bytes() == data
    assert next(iter(hashes.values())) == hashlib.sha256(data).hexdigest()


def test_duplicate_names_and_repeated_uploads_get_independent_evidence(tmp_path):
    first, hashes, _ = receive([upload(filename="same.png"), upload(filename="same.png")], tmp_path,
                               panels="pdp,back")
    second, _, _ = receive([upload(filename="same.png")], tmp_path)
    assert len({c.path for c in first + second}) == 3 and len(hashes) == 2
    assert [c.panel for c in first] == [Panel.PDP, Panel.BACK]


def test_default_panel_does_not_attest_unassigned_sides(tmp_path):
    captures, _, _ = receive([upload(), upload(), upload()], tmp_path)
    assert [c.panel for c in captures] == [Panel.PDP, Panel.UNKNOWN, Panel.UNKNOWN]


@pytest.mark.parametrize("edits", ["not-json", "{}", "null", "[null]", "[[]]",
    '[{"rotation":45}]', '[{"rotation":"90"}]', '[{"rotation":false}]',
    '[{"rotation":90.0}]', '[{"crop":[0,0,1]}]',
    '[{"crop":[-0.1,0,1,1]}]', '[{"crop":[0,0,1.1,1]}]',
    '[{"crop":[0.5,0,0.4,1]}]', '[{"crop":[0,0,0.1,0.1]}]',
    '[{"crop":[0,0,NaN,1]}]', '[{"crop":[0,0,Infinity,1]}]'])
def test_malformed_capture_edits_are_rejected_and_cleaned(tmp_path, edits):
    with pytest.raises(CaptureError):
        receive([upload()], tmp_path, edits=edits)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("edits", ['[{"crop":[false,false,true,true]}]', '[{},{}]'])
def test_ambiguous_edit_payloads_are_rejected(tmp_path, edits):
    with pytest.raises(CaptureError):
        receive([upload()], tmp_path, edits=edits)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("geometry", ['[]', 'null', 'bad-json', '{"pdp_width_mm":100}',
    '{"pdp_width_mm":true,"pdp_height_mm":200}',
    '{"pdp_width_mm":0,"pdp_height_mm":200}',
    '{"pdp_width_mm":5001,"pdp_height_mm":200}',
    '{"pdp_width_mm":NaN,"pdp_height_mm":200}'])
def test_invalid_physical_geometry_is_rejected_and_cleaned(tmp_path, geometry):
    with pytest.raises(CaptureError):
        receive([upload()], tmp_path, dimensions=geometry)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [10 ** 800, -(10 ** 800)])
@pytest.mark.parametrize("field", ["crop", "pdp_width_mm", "pdp_height_mm"])
def test_extreme_json_numbers_are_validation_errors_without_orphan_evidence(tmp_path, value, field):
    arguments = ({"edits": json.dumps([{"crop": [0, 0, value, 1]}])} if field == "crop" else
                 {"dimensions": json.dumps({"pdp_width_mm": 100, "pdp_height_mm": 200,
                                             field: value})})
    with pytest.raises(CaptureError) as caught:
        receive([upload()], tmp_path, **arguments)
    assert caught.value.status == 422
    assert list(tmp_path.iterdir()) == []


def test_dimensions_are_pdp_only_and_cannot_inject_exact_scale(tmp_path):
    captures, _, _ = receive([upload(), upload()], tmp_path, panels="pdp,back",
        dimensions='{"pdp_width_mm":100,"pdp_height_mm":200,"mm_per_px":0.01,"mm_per_px_source":"artwork"}')
    meta = json.loads(Path(captures[0].path).with_suffix(".meta.json").read_text())
    assert meta == {"pdp_width_mm": 100, "pdp_height_mm": 200}
    assert not Path(captures[1].path).with_suffix(".meta.json").exists()


def test_partially_received_invalid_bundle_leaves_no_orphan_files(tmp_path):
    with pytest.raises(CaptureError):
        receive([upload(), upload(b"not an image")], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_upload_count_dimensions_and_format_are_server_validated(tmp_path):
    for files, status in (([], 400), ([upload() for _ in range(13)], 413),
                          ([upload(picture((15, 60)))], 413),
                          ([upload(picture(fmt="GIF"))], 415)):
        with pytest.raises(CaptureError) as caught:
            receive(files, tmp_path)
        assert caught.value.status == status
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def queue_case(tmp_path):
    repo = Repository(tmp_path / "queue.db")
    rules = RulesEngine.from_directory()
    repo.archive_rules(rules.pack)
    queue = InspectionJobs(repo, rules)
    captures, hashes, edits = receive([upload()], tmp_path / "uploads")
    yield SimpleNamespace(repo=repo, rules=rules, queue=queue, captures=captures,
                          hashes=hashes, edits=edits, directory=tmp_path)
    queue.stop()


def queue_input(case, **overrides):
    values = {"actor": OWNER, "lane": "field", "complete": False, "region": "Delhi",
              "geo": (28.6139, 77.209), "concerns": ["milk"], "parent": None}
    values.update(overrides)
    return case.queue.enqueue(case.captures, case.hashes, case.edits, **values)


def analysis_stub(captures, options, *, rules):
    """Only queue tests use this; it is not an OCR recognizer or accuracy test."""
    assert options.engine_name == "rapidocr"
    return Analysis(scan=Scan(scan_id="TEMPORARY", frames=[c.path for c in captures],
                             frame_hashes={c.path: sha256_file(c.path) for c in captures},
                             operator=options.operator, lane=options.lane, geo=getattr(options, "geo", None)),
                    package=PackageFacts(gtin="8901234567890"), engine="queue-test-analysis-stub",
                    rules_version=rules.pack.version,
                    findings=[Finding(finding_id="temporary-finding", rule_id="QUEUE.CONTRACT",
                                      rules_version=rules.pack.version, citation=Citation(),
                                      verdict=Verdict.INCONCLUSIVE, message="Queue-contract test observation")])


def wait_until(queue, job_id, state, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = queue.get(job_id)
        if job and job["state"] == state:
            return job
        time.sleep(.02)
    pytest.fail(f"Job did not reach {state}: {queue.get(job_id)}")


def test_worker_persists_truthful_progress_original_evidence_and_history(queue_case, monkeypatch):
    case = queue_case
    entered, release = threading.Event(), threading.Event()
    observed = []
    monkeypatch.setenv("TULA_OCR", "fixture")
    parent = analysis_stub(case.captures, SimpleNamespace(engine_name="rapidocr", operator=OWNER.display_name,
                                                          lane=Lane.FIELD), rules=case.rules)
    parent.scan.scan_id = "PRIOR-SCAN"
    parent.scan.inspector_id = OWNER.id
    parent.scan.original_frame_hashes = case.hashes
    parent.scan.capture_edits = case.edits
    case.repo.save(parent)
    case.captures, case.hashes, case.edits = receive([upload()], case.directory / "closeup")

    def controlled_analysis(captures, options, *, rules):
        observed.append(options)
        options.progress("ocr", "OCR pass is running")
        entered.set()
        assert release.wait(5)
        options.progress("rules", "Evaluating applicable rules")
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", controlled_analysis)
    job = queue_input(case, parent=parent.scan.scan_id)
    try:
        assert entered.wait(5)
        running = case.queue.get(job["id"])
        assert running["state"] == "running" and running["stage"] == "ocr"
        assert running["detail"] == "OCR pass is running"
        assert running["scan_id"] is None
    finally:
        release.set()
    saved = wait_until(case.queue, job["id"], "complete")
    assert saved["stage"] == "saved" and saved["attempts"] == 1
    assert saved["owner_id"] == OWNER.id
    a = Repository(case.repo.path).get(saved["scan_id"])
    assert a.scan.inspector_id == OWNER.id and a.scan.region == "Delhi"
    assert a.scan.geo == pytest.approx((28.6139, 77.209))
    assert a.scan.parent_scan_id == "PRIOR-SCAN"
    assert a.scan.parent_revision == 0 and a.scan.parent_record_sha256
    assert a.scan.frames == parent.scan.frames + [c.path for c in case.captures]
    assert a.scan.original_frame_hashes == {**parent.scan.original_frame_hashes, **case.hashes}
    assert a.scan.capture_edits == json.loads(json.dumps(parent.scan.capture_edits + case.edits))
    assert all(f.finding_id.startswith(f"F-{a.scan.scan_id}-") for f in a.findings)
    assert case.repo.revision(a.scan.scan_id, 0) is not None
    assert {row.scan_id for row in case.repo.search()} == {a.scan.scan_id, parent.scan.scan_id}
    assert len(case.repo.history(a.package.gtin)) == 2
    assert observed[0].engine_name == "rapidocr" and observed[0].lane is Lane.FIELD
    assert observed[0].geo == pytest.approx((28.6139, 77.209))
    assert observed[0].allergen_concerns == ["milk"]


def test_failed_job_retains_upload_and_retry_saves_only_one_inspection(queue_case, monkeypatch):
    calls = []

    def fail_once(captures, options, *, rules):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("private model detail must not reach client")
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", fail_once)
    job = queue_input(queue_case)
    failed = wait_until(queue_case.queue, job["id"], "failed")
    assert "private model detail" not in failed["error"]
    assert failed["attempts"] == 1 and not queue_case.repo.search()
    assert all(Path(p).exists() for p in queue_case.hashes)
    queue_case.queue.retry(job["id"])
    saved = wait_until(queue_case.queue, job["id"], "complete")
    assert saved["attempts"] == 2 and saved["error"] is None
    assert len(queue_case.repo.search()) == 1
    with pytest.raises(ValueError, match="Only failed"):
        queue_case.queue.retry(job["id"])
    assert len(calls) == 2


def test_restart_drains_saved_queued_job_without_reupload(queue_case, monkeypatch):
    case = queue_case
    monkeypatch.setattr(case.queue, "start", lambda: None)
    job = queue_input(case)
    assert job["state"] == "queued"
    monkeypatch.setattr(jobs_module, "analyse", analysis_stub)
    restarted = InspectionJobs(Repository(case.repo.path), case.rules)
    try:
        assert restarted.get(job["id"])["owner_id"] == OWNER.id
        restarted.start()
        saved = wait_until(restarted, job["id"], "complete")
        assert saved["attempts"] == 1 and case.repo.get(saved["scan_id"])
    finally:
        restarted.stop()


def test_expired_running_lease_becomes_retryable_and_recent_lease_stays_running(queue_case, monkeypatch):
    case = queue_case
    original_start = case.queue.start
    monkeypatch.setattr(case.queue, "start", lambda: None)
    old, recent = queue_input(case), queue_input(case)
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='running',attempts=1,updated_at=? WHERE id=?",
                     (time.time() - 1000, old["id"]))
        conn.execute("UPDATE inspection_job SET state='running',attempts=1 WHERE id=?", (recent["id"],))
    original_start()
    failed = wait_until(case.queue, old["id"], "failed")
    assert "Retry" in failed["error"] and case.queue.get(recent["id"])["state"] == "running"
    assert all(Path(p).exists() for p in case.hashes)


def test_queue_capacity_is_enforced_in_database(queue_case, monkeypatch):
    case = queue_case
    monkeypatch.setattr(case.queue, "start", lambda: None)
    ids = {queue_input(case)["id"] for _ in range(12)}
    assert len(ids) == 12
    with pytest.raises(ValueError, match="queue is full"):
        queue_input(case)
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection_job").fetchone()[0] == 12


def test_retry_cannot_bypass_queue_capacity(queue_case, monkeypatch):
    case = queue_case
    monkeypatch.setattr(case.queue, "start", lambda: None)
    failed = queue_input(case)
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='failed' WHERE id=?", (failed["id"],))
    for _ in range(12):
        queue_input(case)
    with pytest.raises(ValueError, match="queue is full"):
        case.queue.retry(failed["id"])
    assert case.queue.get(failed["id"])["state"] == "failed"


def test_retry_after_interruption_preserves_already_saved_history(queue_case, monkeypatch):
    calls = []

    def counted_analysis(captures, options, *, rules):
        calls.append(1)
        return analysis_stub(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", counted_analysis)
    job = queue_input(queue_case)
    saved = wait_until(queue_case.queue, job["id"], "complete")
    before = queue_case.repo.get(saved["scan_id"]).model_dump(mode="json")
    with queue_case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='failed' WHERE id=?", (job["id"],))
    queue_case.queue.retry(job["id"])
    wait_until(queue_case.queue, job["id"], "complete")
    assert queue_case.repo.get(saved["scan_id"]).model_dump(mode="json") == before
    assert len(queue_case.repo.revisions(saved["scan_id"])) == 1
    assert len(queue_case.repo.search()) == 1


def test_worker_analysis_audit_is_recorded_with_identity(queue_case, monkeypatch):
    events = []
    queue_case.queue.security = SimpleNamespace(audit=lambda **event: events.append(event))
    monkeypatch.setattr(jobs_module, "analyse", analysis_stub)
    job = queue_input(queue_case)
    saved = wait_until(queue_case.queue, job["id"], "complete")
    queue_case.queue.stop()
    assert len(events) == 1
    assert events[0]["actor_id"] == OWNER.id and events[0]["action"] == "inspection.analysed"
    assert events[0]["entity_id"] == saved["scan_id"]


def test_http_job_status_and_retry_require_owner_or_supervisor(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "http.db")
    security = SecurityStore(repo.path)
    admin = security.create_user("admin", PASSWORD, role="admin", bootstrap=True)
    owner = security.create_user("owner", PASSWORD, role="inspector", actor_id=admin.id)
    security.create_user("other", PASSWORD, role="inspector", actor_id=admin.id)
    security.create_user("supervisor", PASSWORD, role="supervisor", actor_id=admin.id)
    app = FastAPI()
    install_security(app, repo.path)
    app.state.security = security
    rules = RulesEngine.from_directory()
    queue = InspectionJobs(repo, rules, security)
    monkeypatch.setattr(queue, "start", lambda: None)
    app.state.jobs = queue
    web = SimpleNamespace(app=app, repo=repo, rules=rules, UPLOADS=tmp_path / "uploads")
    install_workflow(web)
    captures, hashes, edits = receive([upload()], web.UPLOADS)
    job = queue.enqueue(captures, hashes, edits, actor=owner, lane="field", complete=False)
    with repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='failed' WHERE id=?", (job["id"],))

    def login(client, username):
        client.get("/login")
        response = client.post("/login", data={"username": username, "password": PASSWORD,
                             "csrf_token": client.cookies[LOGIN_CSRF_COOKIE]}, follow_redirects=False)
        assert response.status_code == 303
        return client.get("/v1/session").json()["csrf_token"]

    with TestClient(app, base_url="https://testserver") as client:
        assert client.get(f"/v1/jobs/{job['id']}").status_code == 401
        csrf = login(client, "other")
        assert client.get(f"/v1/jobs/{job['id']}").status_code == 404
        assert client.post(f"/v1/jobs/{job['id']}/retry", headers={"X-CSRF-Token": csrf}).status_code == 404
        client.cookies.clear()
        csrf = login(client, "owner")
        status = client.get(f"/v1/jobs/{job['id']}")
        assert status.status_code == 200 and status.json()["owner_id"] == owner.id
        assert "payload" not in status.json()
        assert client.post(f"/v1/jobs/{job['id']}/retry").status_code == 403
        assert client.post(f"/v1/jobs/{job['id']}/retry", headers={"X-CSRF-Token": csrf}).status_code == 200
        client.cookies.clear()
        login(client, "supervisor")
        assert client.get(f"/v1/jobs/{job['id']}").status_code == 200
