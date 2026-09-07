"""Real SQLite revisions and authenticated HTTP for supplementary corrections."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel, Verdict
from tula.domain.models import (
    Analysis,
    Citation,
    Declaration,
    Finding,
    PackageFacts,
    ReviewState,
    Scan,
    sha256_file,
)
from tula.extract.intelligence import extract_intelligence
from tula.ocr.base import OcrLine
from tula.security import User, install_security
from tula.security.web import LOGIN_CSRF_COOKIE
from tula.services import review
from tula.services.intelligence_review import FIELD_LABELS, correct_observation
from tula.storage.db import Repository
from tula.storage.workflow import RevisionConflict
from tula.web.intelligence_review import install_intelligence_review

OWNER = User("owner", "owner", "Assigned Inspector", "inspector", True)
OTHER = User("other", "other", "Other Inspector", "inspector", True)
SUPERVISOR = User("supervisor", "supervisor", "Review Supervisor", "supervisor", True)
REASON = "Read the complete printed marking in the original source image."
PASSWORD = "Supplementary test passphrase 2026!"


@pytest.fixture
def case(tmp_path):
    frame, original = tmp_path / "label.png", tmp_path / "original.png"
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    for y, text in [(25, "MFG 01/04/2026"), (80, "Packed on 01/05/2026"), (135, "EXP 08/09/26"),
                    (190, "Best before 12 months from manufacture"), (245, "Use by 31/12/2026"),
                    (300, "Batch LOT-002"), (355, "Ingredients: Wheat flour, INS 322"),
                    (410, "Imported by Sample Foods, Mumbai")]:
        draw.text((20, y), text, fill="black", font_size=22)
    image.save(frame)
    original.write_bytes(frame.read_bytes())
    name = str(frame)
    # Deliberate OCR errors are corrected against the generated source image.
    intel = extract_intelligence([
        (Panel.BACK, OcrLine("EXP 08/09/26", (15, 130, 400, 165), confidence=.62, frame=name)),
        (Panel.BACK, OcrLine("Ingredients: Milk powder, INS 330", (15, 350, 700, 385), confidence=.57, frame=name)),
    ], allergen_concerns=["milk", "gluten"])
    a = Analysis(scan=Scan(scan_id="INTEL-REVIEW-QA", frames=[name], inspector_id=OWNER.id,
        operator=OWNER.display_name, packing_date=date(2026, 4, 1), source="bench",
        frame_hashes={name: sha256_file(name)}, original_frame_hashes={str(original): sha256_file(str(original))},
        image_diagnostics=[{"frame": name, "panel": "back", "width": 800, "height": 500}]),
        package=PackageFacts(), rules_version="review-fixture", intelligence=intel,
        declarations={DC.DATE_OF_PACKING: Declaration(klass=DC.DATE_OF_PACKING, raw="Packed on 01/04/2026",
                        norm={"year": 2026, "month": 4}, frame=name, bbox=(15, 75, 450, 110))},
        findings=[Finding(finding_id="F-QA", rule_id="QA.CHECK", rules_version="review-fixture",
                           citation=Citation(clause="Synthetic review fixture"), verdict=Verdict.PASS)])
    repo = Repository(tmp_path / "review.db")
    repo.save(a)
    return SimpleNamespace(repo=repo, a=a, frame=frame, original=original, path=tmp_path)


def correct(case, *, revision=0, actor=OWNER, field="ingredients", raw="Ingredients: Wheat flour, INS 322",
            frame_index=0, bbox=(15, 350, 700, 385), reason=REASON, index=0, interpretation=""):
    return correct_observation(case.repo, case.a.scan.scan_id, revision, actor, field, raw,
                               frame_index, bbox, reason, observation_index=index,
                               date_interpretation=interpretation)


def test_correction_retains_original_transcription_source_and_revision(case):
    before = case.repo.revision(case.a.scan.scan_id, 0).model_dump_json()
    updated = correct(case)
    item = updated.intelligence["fields"]["ingredients"][0]
    assert item["value"] == "Wheat flour, INS 322"
    assert item["original_transcription"] == "Ingredients: Milk powder, INS 330"
    assert item["original_sources"][0]["text"] == item["original_transcription"]
    assert item["ocr_confidence"] == .57 and item["extraction_confidence"] is None
    source = item["sources"][0]
    assert source["image_index"] == 0 and source["panel"] == "back"
    assert (source["image_width"], source["image_height"]) == (800, 500)
    assert source["bbox"] == [15, 350, 700, 385]
    assert item["method"] == "inspector_correction"
    correction = updated.review.corrections[-1]
    assert correction["kind"] == "intelligence.ingredients" and correction["actor_id"] == OWNER.id
    assert correction["before"]["raw"] == item["original_transcription"]
    assert correction["after"] == item
    assert case.repo.revision(case.a.scan.scan_id, 0).model_dump_json() == before
    assert updated.review.revision == 1 and updated.review.status == "in_review"
    assert case.repo.revisions(case.a.scan.scan_id)[0]["action"] == "intelligence.corrected"


def test_ingredient_correction_refreshes_allergens_and_additive_identifiers(case):
    updated = correct(case)
    allergens = updated.intelligence["allergens"]
    assert allergens["concerns"] == ["milk", "gluten"]
    assert allergens["unmatched"] == ["milk"]
    assert {(match["concern"], match["kind"]) for match in allergens["matches"]} == {("gluten", "possible")}
    assert [item["code"] for item in updated.intelligence["additives"]] == ["INS 322"]
    assert updated.intelligence["additives"][0]["sources"][0]["text"] == "Ingredients: Wheat flour, INS 322"


def test_repeated_correction_preserves_first_ocr_and_each_before_after(case):
    first = correct(case)
    second = correct(case, revision=1, raw="Ingredients: Wheat flour")
    assert second.intelligence["fields"]["ingredients"][0]["original_transcription"] == "Ingredients: Milk powder, INS 330"
    assert second.review.corrections[-1]["before"] == first.intelligence["fields"]["ingredients"][0]
    assert len(case.repo.revisions(case.a.scan.scan_id)) == 3


@pytest.mark.parametrize(("field", "raw", "expected"), [
    ("manufacturing_date", "MFG 2026-04-01", "2026-04-01"),
    ("packing_date", "Packed on 2026-05-01", "2026-05-01"),
    ("expiry_date", "EXP 2026-09-08", "2026-09-08"),
    ("best_before", "Best before 12 months from manufacture", {"duration": 12, "unit": "month", "reference": "manufacturing_date"}),
    ("use_by", "Use by 31/12/2026", "2026-12-31"),
    ("batch_number", "Lot LOT-002", "LOT-002"),
    ("ingredients", "Ingredients: Wheat flour, INS 322", "Wheat flour, INS 322"),
    ("importer", "Imported by Sample Foods, Mumbai", "Imported by Sample Foods, Mumbai"),
])
def test_every_displayed_field_supports_adding_located_observation(case, field, raw, expected):
    a = correct(case, field=field, raw=raw, index=-1)
    item = a.intelligence["fields"][field][-1]
    assert item["value"] == expected
    assert item["original_transcription"] is None and item["ocr_confidence"] is None
    assert item["extraction_confidence"] is None


@pytest.mark.parametrize("raw", ["EXP 08/09/26", "EXP 09/2026", "EXP 31/02/2026"])
def test_date_corrections_do_not_invent_a_resolved_ambiguous_or_invalid_date(case, raw):
    a = correct(case, field="expiry_date", raw=raw)
    item = a.intelligence["fields"]["expiry_date"][0]
    if raw == "EXP 09/2026":
        assert item["value"] == "2026-09"  # Preserve month precision.
    else:
        assert item["value"] is None and item["status"] == "needs_review"
    assert "temporal_status" not in item


def test_short_year_requires_explicit_interpretation_even_with_one_candidate(case):
    a = correct(case, field="expiry_date", raw="EXP 31/12/26")
    item = a.intelligence["fields"]["expiry_date"][0]
    assert item["value"] is None and item["candidates"][0]["iso"] == "2026-12-31"
    assert "2000" in item["warning"]


@pytest.mark.parametrize("raw", ["LOT-002", "Batch: LOT-002", "Batch - LOT-002"])
def test_batch_code_that_starts_with_lot_is_preserved(case, raw):
    a = correct(case, field="batch_number", raw=raw, index=-1)
    assert a.intelligence["fields"]["batch_number"][0]["value"] == "LOT-002"


def test_explicit_date_interpretation_is_audited_and_retains_both_candidates(case):
    a = correct(case, field="expiry_date", raw="EXP 08/09/26", interpretation="2026-09-08")
    item = a.intelligence["fields"]["expiry_date"][0]
    assert item["value"] == "2026-09-08" and item["status"] == "officer_interpreted"
    assert {candidate["iso"] for candidate in item["candidates"]} == {"2026-08-09", "2026-09-08"}
    assert item["date_interpretation"] == "2026-09-08"
    assert a.review.corrections[-1]["after"]["date_interpretation"] == "2026-09-08"


def test_unsupported_date_interpretation_cannot_override_printed_evidence(case):
    with pytest.raises(ValueError, match="date candidates"):
        correct(case, field="expiry_date", raw="EXP 08/09/26", interpretation="2027-09-08")
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


def test_date_cue_cannot_be_reclassified_as_a_different_printed_date(case):
    with pytest.raises(ValueError, match="does not match"):
        correct(case, field="packing_date", raw="EXP 2026-09-08", index=-1)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


def test_statutory_declarations_findings_and_packing_date_do_not_change(case):
    before = case.a.model_dump(mode="json")
    a = correct(case, field="packing_date", raw="Packed on 2026-05-01", index=-1)
    assert a.model_dump(mode="json")["declarations"] == before["declarations"]
    assert a.model_dump(mode="json")["findings"] == before["findings"]
    assert a.scan.packing_date == date(2026, 4, 1)
    assert "main declaration correction workflow" in a.review.comments[-1]["text"]


def test_other_inspector_cannot_correct_an_owned_inspection(case):
    with pytest.raises(PermissionError, match="assigned inspector"):
        correct(case, actor=OTHER)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


def test_supervisor_can_correct_but_cannot_approve_own_contribution(case):
    a = correct(case, actor=SUPERVISOR)
    a = review.transition(case.repo, a.scan.scan_id, 1, OWNER, "submit", REASON)
    with pytest.raises(PermissionError, match="did not correct"):
        review.transition(case.repo, a.scan.scan_id, a.review.revision, SUPERVISOR, "approve", REASON)


@pytest.mark.parametrize("status", ["submitted", "approved"])
def test_submission_invalidates_and_approved_record_requires_reopening(case, status):
    def seed(a):
        a.review = ReviewState(status=status, submitted_by=OWNER.id,
                               approved_by="Earlier supervisor", approved_at=datetime.now(UTC),
                               approval_reason="Seeded prior approval state")
    a = case.repo.revise(case.a.scan.scan_id, 0, OWNER.id, "qa.seed", REASON, seed)
    if status == "approved":
        with pytest.raises(ValueError, match="approved"):
            correct(case, revision=a.review.revision, actor=SUPERVISOR)
        assert case.repo.get(a.scan.scan_id).review.status == "approved"
    else:
        a = correct(case, revision=a.review.revision)
        assert a.review.status == "in_review" and a.review.submitted_by is None
        assert a.review.approved_by is None and a.review.approved_at is None and not a.review.approval_reason


def test_stale_edit_preserves_newer_observation_and_revision(case):
    current = correct(case)
    with pytest.raises(RevisionConflict):
        correct(case, raw="Ingredients: Milk")
    assert case.repo.get(case.a.scan.scan_id).model_dump_json() == current.model_dump_json()


@pytest.mark.parametrize(("target", "damage"), [("frame", "missing"), ("frame", "changed"), ("original", "missing"), ("original", "changed")])
def test_original_and_working_evidence_must_both_verify(case, target, damage):
    path = getattr(case, target)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed evidence")
    with pytest.raises(ValueError, match="missing or changed"):
        correct(case)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


@pytest.mark.parametrize("bbox", [[], [1, 2, 3], [0, 0, 0, 20], [-1, 0, 20, 20],
                                    [0, 0, 801, 20], [0, 0, 20, 501], [True, 0, 20, 20],
                                    [0, 0, 20.5, 30], [0, 0, float("nan"), 30]])
def test_invalid_rectangles_never_create_a_revision(case, bbox):
    with pytest.raises(ValueError, match="rectangle|pixel bounds"):
        correct(case, bbox=bbox)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


@pytest.mark.parametrize("arguments", [{"index": 90}, {"index": -2}, {"frame_index": 90},
                                      {"field": "retail_sale_price"}, {"raw": ""}, {"reason": "no"}])
def test_invalid_target_or_reason_does_not_mutate_stored_record(case, arguments):
    with pytest.raises(ValueError):
        correct(case, **arguments)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


@pytest.fixture
def http_case(case):
    app = FastAPI()
    store = install_security(app, case.repo.path)
    admin = store.create_user("review-admin", PASSWORD, role="admin", bootstrap=True)
    inspector = store.create_user("review-inspector", PASSWORD, role="inspector", actor_id=admin.id)
    case.repo.revise(case.a.scan.scan_id, 0, inspector.id, "qa.owner", REASON,
                    lambda a: setattr(a.scan, "inspector_id", inspector.id))
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "src/tula/web/templates"))
    web = SimpleNamespace(app=app, repo=case.repo, templates=templates)
    install_intelligence_review(web)

    @app.get("/inspections/{scan_id}")
    def show(request: Request, scan_id: str):
        return templates.TemplateResponse(request, "_intelligence.html", {
            "a": case.repo.get(scan_id), "csrf_token": request.state.csrf_token})

    @app.get("/inspections/{scan_id}/frames/{index}")
    def frame(scan_id: str, index: int):
        return FileResponse(case.frame)

    with TestClient(app, base_url="https://testserver") as client:
        yield SimpleNamespace(client=client, store=store, case=case, inspector=inspector)


def login(http_case):
    client = http_case.client
    client.get("/login")
    response = client.post("/login", data={"username": "review-inspector", "password": PASSWORD,
                                          "csrf_token": client.cookies[LOGIN_CSRF_COOKIE]}, follow_redirects=False)
    assert response.status_code == 303
    return client.get("/v1/session").json()["csrf_token"]


def payload(**overrides):
    return {"revision": "1", "field": "ingredients", "observation_index": "0",
            "raw": "Ingredients: Wheat flour, INS 322", "frame_index": "0",
            "bbox": "[]", "left": "15", "top": "350", "right": "700", "bottom": "385",
            "reason": REASON, **overrides}


def test_http_requires_authentication_and_real_csrf(http_case):
    url = f"/inspections/{http_case.case.a.scan.scan_id}/intelligence/correct"
    assert http_case.client.post(url, data=payload(), follow_redirects=False).status_code in (401, 303)
    token = login(http_case)
    assert http_case.client.post(url, data=payload(), follow_redirects=False).status_code == 403
    response = http_case.client.post(url, data=payload(csrf_token=token), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("#label-intelligence")
    events = http_case.store.audit_events(entity_id=http_case.case.a.scan.scan_id)
    assert any(event["action"] == "intelligence.corrected" for event in events)


def test_http_keyboard_rectangle_form_and_updated_template(http_case):
    token = login(http_case)
    path = f"/inspections/{http_case.case.a.scan.scan_id}"
    before = http_case.client.get(path)
    assert before.status_code == 200
    for field in FIELD_LABELS:
        assert f'value="{field}"' in before.text
    assert "Correct this observation" in before.text and 'name="left"' in before.text
    response = http_case.client.post(path + "/intelligence/correct", data=payload(csrf_token=token), follow_redirects=False)
    assert response.status_code == 303, response.text
    after = http_case.client.get(path)
    assert "Original transcription" in after.text and "Ingredients: Milk powder, INS 330" in after.text
    assert "Corrected by" in after.text and "Wheat flour, INS 322" in after.text
    stale = http_case.client.post(path + "/intelligence/correct", data=payload(csrf_token=token), follow_redirects=False)
    assert stale.status_code == 409


def test_http_new_observation_without_ocr_score_renders_and_invalid_json_is_actionable(http_case):
    token = login(http_case)
    path = f"/inspections/{http_case.case.a.scan.scan_id}"
    invalid = http_case.client.post(path + "/intelligence/correct", data=payload(csrf_token=token, bbox="not json"))
    assert invalid.status_code == 422
    response = http_case.client.post(path + "/intelligence/correct", data=payload(csrf_token=token,
        field="batch_number", raw="Batch LOT-002", observation_index="-1"), follow_redirects=False)
    assert response.status_code == 303
    page = http_case.client.get(path)
    assert page.status_code == 200 and "No original OCR score is recorded" in page.text
