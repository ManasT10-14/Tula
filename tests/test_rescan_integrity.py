"""Rescan integrity uses real files/SQLite/HTTP and an explicit OCR test adapter."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from tula.analyse import analyse
from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import ExtractionPath
from tula.domain.models import Analysis, Declaration, PackageFacts, Scan, sha256_file
from tula.ocr.base import OcrLine, OcrResult
from tula.rules.engine import RulesEngine
from tula.security import User, install_security
from tula.services import jobs as jobs_module
from tula.services.capture import receive
from tula.services.capture_drafts import CaptureDrafts, DraftError
from tula.services.jobs import InspectionJobs
from tula.services.rescan import RescanError
from tula.storage.db import Repository
from tula.web.workflow import install_workflow

OWNER = User("rescan-owner", "rescan-owner", "Assigned Inspector", "inspector", True)


def image_bytes(color="white"):
    data = BytesIO()
    Image.new("RGB", (160, 120), color).save(data, "PNG")
    return data.getvalue()


def upload(color="white"):
    return UploadFile(filename="label.png", file=BytesIO(image_bytes(color)))


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "data" / "tula.db")
    rules = RulesEngine.from_directory()
    rules = RulesEngine(rules.pack.model_copy(deep=True, update={"version": "captured-parent-rules"}))
    repo.archive_rules(rules.pack)
    captures, hashes, edits = receive([upload()], tmp_path / "parent", dimensions='{"pdp_width_mm":80,"pdp_height_mm":100}')
    parent = Analysis(scan=Scan(scan_id="RESCAN-PARENT", inspector_id=OWNER.id,
        frames=[c.path for c in captures], frame_hashes={c.path: sha256_file(c.path) for c in captures},
        original_frame_hashes=hashes, capture_edits=edits, packing_date=date(2020, 2, 1)),
        package=PackageFacts(legal_context={"assessment_date": "2020-03-15", "assessment_date_confirmed": True}),
        rules_version=rules.pack.version,
        declarations={DC.RETAIL_SALE_PRICE: Declaration(klass=DC.RETAIL_SALE_PRICE, raw="MRP Rs 100",
            norm={"value": 100, "verified_transcription": True}, path=ExtractionPath.MANUAL,
            frame=captures[0].path, bbox=(1, 1, 100, 30))})
    parent.review.corrections.append({"actor_id": "correcting-supervisor", "kind": "retail_sale_price",
                                      "reason": "Verified against the original price panel"})
    repo.save(parent)
    fresh = receive([upload("blue")], tmp_path / "closeup", panels="back")
    queue = InspectionJobs(repo, rules)
    monkeypatch.setattr(queue, "start", lambda: True)
    observed = []

    def explicit_analysis_adapter(captures, options, *, rules):
        observed.append((captures, options, rules))
        return Analysis(scan=Scan(scan_id="temporary", frames=[c.path for c in captures],
            frame_hashes={c.path: sha256_file(c.path) for c in captures}), package=PackageFacts(legal_context=options.legal_context),
            rules_version=rules.pack.version, declarations={DC.RETAIL_SALE_PRICE: Declaration(
                klass=DC.RETAIL_SALE_PRICE, raw="MRP Rs 999", norm={"value": 999}, frame=captures[-1].path)})

    monkeypatch.setattr(jobs_module, "analyse", explicit_analysis_adapter)
    return SimpleNamespace(repo=repo, rules=rules, parent=parent, fresh=fresh, queue=queue,
                           observed=observed, adapter=explicit_analysis_adapter, root=tmp_path)


def enqueue(case, **options):
    values = {"actor": OWNER, "lane": "field", "complete": False, "parent": case.parent.scan.scan_id,
              "rescan_target": "retail_sale_price"}
    values.update(options)
    return case.queue.enqueue(*case.fresh, **values)


def payload(case, job):
    with case.repo._connect() as conn:
        return json.loads(conn.execute("SELECT payload FROM inspection_job WHERE id=?", (job["id"],)).fetchone()[0])


def advance_parent(case):
    return case.repo.revise(case.parent.scan.scan_id, 0, OWNER.id, "review.comment", "Later review note",
                            lambda a: a.review.comments.append({"text": "Later comment"}))


def test_captured_revision_pack_date_and_assertions_survive_later_review(case):
    replacement = case.rules.pack.model_copy(deep=True, update={"version": "new-active-rules"})
    case.repo.archive_rules(replacement)
    with case.repo._connect() as conn:
        conn.execute("CREATE TABLE app_configuration (key TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO app_configuration VALUES ('active_rule_version',?)", (replacement.version,))
        retained = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=0",
                                (case.parent.scan.scan_id,)).fetchone()[0]
    job = enqueue(case, legal_context={"assessment_date": "2099-01-01", "category": "food", "category_confirmed": False})
    saved_payload = payload(case, job)
    assert saved_payload["parent_snapshot"]["record"] == retained
    advance_parent(case)
    case.queue._run(case.queue._claim(job["id"]))
    completed = case.queue.get(job["id"])
    assert completed["state"] == "complete", completed
    child = case.repo.get(completed["scan_id"])
    assert child.scan.parent_revision == 0
    assert child.scan.parent_record_sha256 == hashlib.sha256(retained.encode()).hexdigest()
    assert child.scan.rescan_target == "retail_sale_price"
    assert child.rules_version == case.parent.rules_version != replacement.version
    assert child.package.legal_context["assessment_date"] == "2020-03-15"
    assert case.observed[0][1].packing_date == date(2020, 2, 1)
    assert [c.path for c in case.observed[0][0]] == case.parent.scan.frames + [case.fresh[0][0].path]
    assert child.declarations[DC.RETAIL_SALE_PRICE].raw == "MRP Rs 999"
    assert child.review.status == "draft" and not child.review.decisions and not child.review.corrections
    original = case.repo.revision(case.parent.scan.scan_id, child.scan.parent_revision)
    assert original.declarations[DC.RETAIL_SALE_PRICE].raw == "MRP Rs 100"
    assert original.review.corrections[0]["actor_id"] == "correcting-supervisor"
    assert case.repo.get(case.parent.scan.scan_id).review.revision == 1


@pytest.mark.parametrize("damaged", ["parent_working", "parent_original", "new_working", "new_original", "metadata", "added_metadata"])
def test_evidence_changed_after_enqueue_blocks_ocr_publish_and_retry(case, damaged):
    job = enqueue(case)
    paths = {"parent_working": case.parent.scan.frames[0],
             "parent_original": next(iter(case.parent.scan.original_frame_hashes)),
             "new_working": case.fresh[0][0].path, "new_original": next(iter(case.fresh[1])),
             "metadata": str(Path(case.parent.scan.frames[0]).with_suffix(".meta.json")),
             "added_metadata": str(Path(case.fresh[0][0].path).with_suffix(".meta.json"))}
    source = Path(paths[damaged])
    before = source.read_bytes() if source.exists() else None
    source.write_bytes(b'{"mm_per_px":2}')
    case.queue._run(case.queue._claim(job["id"]))
    failed = case.queue.get(job["id"])
    assert failed["state"] == "failed" and "changed" in failed["error"]
    assert not case.observed and failed["scan_id"] is None
    with pytest.raises(RescanError, match="changed"):
        case.queue.retry(job["id"], actor=OWNER)
    assert case.queue.get(job["id"])["state"] == "failed"
    if before is None:
        source.unlink()
    else:
        source.write_bytes(before)
    advance_parent(case)
    assert case.queue.retry(job["id"], actor=OWNER)["state"] == "queued"
    case.queue._run(case.queue._claim(job["id"]))
    assert case.queue.get(job["id"])["state"] == "complete"
    assert case.repo.get(case.queue.get(job["id"])["scan_id"]).scan.parent_revision == 0


@pytest.mark.parametrize("metadata", [False, True])
def test_change_during_ocr_cannot_publish_rebased_result(case, monkeypatch, metadata):
    job = enqueue(case)

    def changing_adapter(captures, options, *, rules):
        target = Path(captures[0].path)
        if metadata:
            target = target.with_suffix(".meta.json")
        target.write_bytes(b"changed during analysis")
        return case.adapter(captures, options, rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", changing_adapter)
    case.queue._run(case.queue._claim(job["id"]))
    assert case.queue.get(job["id"])["state"] == "failed"
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection").fetchone()[0] == 1


def test_stale_or_unauthorized_service_parent_cannot_enqueue(case):
    advance_parent(case)
    with pytest.raises(RescanError, match="changed"):
        enqueue(case, parent_revision=0)
    outsider = User("other", "other", "Other Inspector", "inspector", True)
    with pytest.raises(RescanError, match="assigned"):
        enqueue(case, actor=outsider)
    with case.repo._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inspection_job").fetchone()[0] == 0


def test_missing_or_changed_captured_revision_blocks_retry(case):
    job = enqueue(case)
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='failed' WHERE id=?", (job["id"],))
        conn.execute("UPDATE inspection_revision SET record=record || ' ' WHERE scan_id=?", (case.parent.scan.scan_id,))
    with pytest.raises(RescanError, match="revision is missing or changed"):
        case.queue.retry(job["id"])


def test_original_index_uses_explicit_frame_mapping_and_leaves_unknown_legacy_source_empty(case):
    # A legacy parent may retain only its working image; the new original must
    # never be assigned to that earlier frame through dictionary insertion order.
    legacy = case.parent.model_copy(deep=True)
    legacy.scan.scan_id = "LEGACY-PARENT"
    legacy.scan.original_frame_hashes = {}
    legacy.scan.capture_edits = []
    case.repo.save(legacy)
    job = enqueue(case, parent=legacy.scan.scan_id)
    case.queue._run(case.queue._claim(job["id"]))
    child_id = case.queue.get(job["id"])["scan_id"]
    with case.repo._connect() as conn:
        rows = conn.execute("SELECT * FROM product_image WHERE scan_id=? ORDER BY frame_index", (child_id,)).fetchall()
    assert len(rows) == 2 and rows[0]["original_path"] is None
    assert rows[1]["original_path"] == case.fresh[2][0]["original"]
    assert rows[1]["original_sha256"] == case.fresh[1][rows[1]["original_path"]]


@pytest.fixture
def http(case):
    app = FastAPI()
    security = install_security(app, case.repo.path)
    admin = security.create_user("rescan-admin", "Disposable rescan test passphrase", role="admin", bootstrap=True)
    session = security.login(admin.username, "Disposable rescan test passphrase")
    case.queue.security = security
    app.state.jobs = case.queue
    web = SimpleNamespace(app=app, repo=case.repo, rules=case.rules, UPLOADS=case.root / "http-uploads")
    install_workflow(web)
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set("tula_session", session.token)
        client.headers["X-CSRF-Token"] = session.session.csrf_token
        yield client, web, security, admin


def test_stale_http_revision_rejected_before_upload_and_legacy_client_gets_current_anchor(case, http):
    client, web, _, _ = http
    advance_parent(case)
    response = client.post("/v1/inspections", data={"parent_scan_id": case.parent.scan.scan_id, "parent_revision": "0"},
        files={"files": ("closeup.png", image_bytes(), "image/png")})
    assert response.status_code == 409 and not web.UPLOADS.exists()
    response = client.post("/v1/inspections", data={"parent_scan_id": case.parent.scan.scan_id, "rescan_target": "packing_date"},
        files={"files": ("closeup.png", image_bytes(), "image/png")})
    assert response.status_code == 202, response.text
    assert payload(case, response.json())["parent_snapshot"]["revision"] == 1


@pytest.mark.parametrize("data", [{"rescan_target": "expiry_date"},
    {"parent_scan_id": "RESCAN-PARENT", "rescan_target": "invented"}, {"parent_revision": "1"}])
def test_invalid_target_or_unlinked_revision_rejected_before_upload(case, http, data):
    client, web, _, _ = http
    response = client.post("/v1/inspections", data=data, files={"files": ("closeup.png", image_bytes(), "image/png")})
    assert response.status_code == 422 and not web.UPLOADS.exists()


@pytest.mark.parametrize("geo", ['[91,77]', '[12,181]', '[12]', '"12,77"', '[true,77]', 'not-json'])
def test_invalid_http_location_is_rejected_before_upload(case, http, geo):
    client, web, _, _ = http
    response = client.post("/v1/inspections", data={"geo": geo},
                           files={"files": ("package.png", image_bytes(), "image/png")})
    assert response.status_code == 422
    assert response.json()["detail"] == "Record a valid latitude and longitude, or clear the location."
    assert not web.UPLOADS.exists()


def test_http_location_is_validated_and_retained_in_queued_payload(case, http):
    client, _, _, _ = http
    response = client.post("/v1/inspections", data={"geo": '[12.9715987,77.5945661]'},
                           files={"files": ("package.png", image_bytes(), "image/png")})
    assert response.status_code == 202, response.text
    assert payload(case, response.json())["geo"] == [12.971599, 77.594566]


def test_draft_resolves_parent_revision_preserves_target_and_blocks_stale_resume(case, http):
    _, _, security, admin = http
    drafts = CaptureDrafts(case.repo, case.root, security)
    draft_id = "1" * 32
    drafts.save(draft_id, 0, "2" * 32, admin, [upload()], details={
        "parent_scan_id": case.parent.scan.scan_id, "rescan_target": "expiry_date"})
    resumed = drafts.get(draft_id, admin)
    assert resumed["details"]["parent_revision"] == 0
    assert resumed["details"]["rescan_target"] == "expiry_date"
    assert resumed["details"]["complete"] is False
    advance_parent(case)
    with pytest.raises(DraftError, match="changed"):
        drafts.get(draft_id, admin)


@pytest.mark.parametrize("kind", ["working", "original", "metadata"])
def test_ordinary_jobs_also_verify_queued_files_before_ocr(case, kind):
    job = enqueue(case, parent=None, rescan_target="")
    path = {"working": Path(case.fresh[0][0].path), "original": Path(next(iter(case.fresh[1]))),
            "metadata": Path(case.fresh[0][0].path).with_suffix(".meta.json")}[kind]
    path.write_bytes(b"changed after ordinary enqueue")
    case.queue._run(case.queue._claim(job["id"]))
    assert case.queue.get(job["id"])["state"] == "failed" and not case.observed


def test_missing_original_after_enqueue_blocks_even_when_working_image_is_intact(case):
    job = enqueue(case)
    Path(next(iter(case.fresh[1]))).unlink()
    case.queue._run(case.queue._claim(job["id"]))
    assert case.queue.get(job["id"])["state"] == "failed" and not case.observed


def test_restored_parent_archive_required_at_retry(case):
    job = enqueue(case)
    with case.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET state='failed' WHERE id=?", (job["id"],))
        conn.execute("UPDATE rule_version SET record=record || ' ' WHERE version=?", (case.parent.rules_version,))
    with pytest.raises(RescanError, match="rule archive is missing or changed"):
        case.queue.retry(job["id"])


def test_combined_pipeline_keeps_both_images_and_deduplicates_same_price(case, monkeypatch):
    class ExplicitMultiImageTestOcr:
        name = "explicit-multi-image-test-adapter"

        def read(self, path):
            texts = (["Net quantity: 200 g", "MRP Rs 45 inclusive of all taxes"]
                     if path == case.parent.scan.frames[0]
                     else ["MRP Rs 45 inclusive of all taxes", "EXP: 12/2027", "Batch No: ABC123"])
            return OcrResult(lines=[OcrLine(text, (2, 2 + i * 20, 155, 18 + i * 20), .99)
                                    for i, text in enumerate(texts)], engine=self.name, width=160, height=120)

    def real_pipeline(captures, options, *, rules):
        return analyse(captures, options, engine=ExplicitMultiImageTestOcr(), rules=rules)

    monkeypatch.setattr(jobs_module, "analyse", real_pipeline)
    job = enqueue(case)
    case.queue._run(case.queue._claim(job["id"]))
    completed = case.queue.get(job["id"])
    assert completed["state"] == "complete", completed
    child = case.repo.get(completed["scan_id"])
    assert child.declarations[DC.NET_QUANTITY].norm["value_base"] == 200
    assert child.declarations[DC.RETAIL_SALE_PRICE].norm["count"] == 1
    assert {span.frame for span in child.spans} == set(child.scan.frames)
    assert len({finding.rule_id for finding in child.findings}) == len(child.findings)
    expiry = child.intelligence["fields"]["expiry_date"]
    assert expiry and expiry[0]["sources"][0]["frame"] == case.fresh[0][0].path
