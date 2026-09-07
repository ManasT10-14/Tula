"""Independent evidence, revision and human-approval workflow regression tests.

Generated labels are explicitly fixture input; these tests exercise workflow
invariants and do not claim OCR accuracy on real photographs.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from tula.bench import run_spec
from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Verdict
from tula.domain.models import sha256_file
from tula.labgen import LabelSpec
from tula.rules.engine import RulesEngine
from tula.security import User, install_security
from tula.services import review
from tula.storage.db import Repository
from tula.storage.workflow import RevisionConflict
from tula.web.workflow import install_workflow

OWNER = User("owner", "owner", "Assigned Inspector", "inspector", True)
OTHER = User("other", "other", "Another Inspector", "inspector", True)
CONTRIBUTOR = User("contributor", "contributor", "Reviewing Supervisor", "supervisor", True)
APPROVER = User("approver", "approver", "Independent Supervisor", "supervisor", True)
REASON = "Verified the printed declaration against the original source image."


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    rules = RulesEngine.from_directory()
    directory = tmp_path_factory.mktemp("review-source")
    result = run_spec(LabelSpec(), rules=rules, work_dir=directory, engine_name="fixture")
    return rules, result.analysis


@pytest.fixture
def case(tmp_path, generated):
    rules, source = generated
    analysis = source.model_copy(deep=True)
    image = tmp_path / "label.png"
    original = tmp_path / "original.bin"
    shutil.copyfile(source.scan.frames[0], image)
    shutil.copyfile(source.scan.frames[0], original)
    analysis.scan.frames = [str(image)]
    analysis.scan.frame_hashes = {str(image): sha256_file(str(image))}
    analysis.scan.original_frame_hashes = {str(original): sha256_file(str(original))}
    analysis.scan.inspector_id = OWNER.id
    analysis.scan.operator = OWNER.display_name
    analysis.scan.source = "inspection"
    for declaration in analysis.declarations.values():
        declaration.frame = str(image)
    for span in analysis.spans:
        span.frame = str(image)
    repo = Repository(tmp_path / "workflow.db")
    repo.archive_rules(rules.pack)
    repo.save(analysis)
    return SimpleNamespace(repo=repo, a=analysis, rules=rules, image=image,
                           original=original, directory=tmp_path)


def resolved(case, actor=OWNER):
    analysis = case.repo.get(case.a.scan.scan_id)
    for finding in list(analysis.pending_review):
        analysis = review.decide(case.repo, analysis.scan.scan_id, analysis.review.revision,
                                 actor, finding.finding_id, "PASS", REASON)
    return analysis


def submit(case, analysis, actor=OWNER):
    return review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                              actor, "submit", "All uncertain findings have been reviewed against evidence.")


def test_manual_uncertainty_on_machine_pass_blocks_submission(case):
    analysis = resolved(case)
    passed = next(f for f in analysis.findings if f.verdict is Verdict.PASS)
    analysis = review.decide(case.repo, analysis.scan.scan_id, analysis.review.revision,
                             OWNER, passed.finding_id, "INCONCLUSIVE", "The image needs a closer readable capture.")
    assert passed.finding_id in {f.finding_id for f in analysis.pending_review}
    with pytest.raises(ValueError, match="Resolve each"):
        submit(case, analysis)
    assert case.repo.get(analysis.scan.scan_id).product_status == "NEEDS_REVIEW"


def test_distinct_supervisor_approval_is_required_for_compliant_status(case):
    analysis = resolved(case)
    assert analysis.product_status == "NEEDS_REVIEW"
    analysis = submit(case, analysis)
    assert analysis.product_status == "NEEDS_REVIEW"
    with pytest.raises(PermissionError, match="Supervisor"):
        review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                           OTHER, "approve", REASON)
    promoted_owner = User(OWNER.id, OWNER.username, OWNER.display_name, "supervisor", True)
    with pytest.raises(PermissionError, match="different supervisor"):
        review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                           promoted_owner, "approve", REASON)
    analysis = review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                                 APPROVER, "approve", REASON)
    assert analysis.product_status == "COMPLIANT"
    assert analysis.review.approved_by == APPROVER.display_name
    assert analysis.review.approved_at is not None
    assert case.repo.analytics()["compliant"] == 1


def test_supervisor_who_decided_findings_cannot_approve_own_work(case):
    analysis = resolved(case, CONTRIBUTOR)
    analysis = submit(case, analysis)
    with pytest.raises(PermissionError, match="did not correct or decide"):
        review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                           CONTRIBUTOR, "approve", REASON)
    assert case.repo.get(analysis.scan.scan_id).review.status == "submitted"


def test_supervisor_who_corrected_text_cannot_approve_own_work(case):
    declaration = case.a.declarations[DC.NET_QUANTITY]
    analysis = review.correct(case.repo, case.rules, case.a.scan.scan_id, 0, CONTRIBUTOR,
                              DC.NET_QUANTITY, declaration.raw, 0, declaration.bbox, REASON)
    analysis = resolved(case, OWNER)
    analysis = submit(case, analysis)
    with pytest.raises(PermissionError, match="did not correct or decide"):
        review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                           CONTRIBUTOR, "approve", REASON)


def test_manual_violation_over_machine_pass_survives_in_report_status(case):
    analysis = resolved(case)
    passed = next(f for f in analysis.findings if f.verdict is Verdict.PASS)
    analysis = review.decide(case.repo, analysis.scan.scan_id, analysis.review.revision,
                             OWNER, passed.finding_id, "VIOLATION", REASON)
    assert passed.finding_id in {f.finding_id for f in analysis.verified_violations}
    analysis = submit(case, analysis)
    analysis = review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                                 APPROVER, "approve", REASON)
    assert analysis.product_status == "NON_COMPLIANT"
    assert case.repo.analytics()["verified_violations"] == 1
    assert case.repo.analytics()["non_compliant"] == 1


def test_approved_record_cannot_be_edited_until_supervisor_reopens(case):
    analysis = submit(case, resolved(case))
    analysis = review.transition(case.repo, analysis.scan.scan_id, analysis.review.revision,
                                 APPROVER, "approve", REASON)
    frozen_revision = analysis.review.revision
    with pytest.raises(ValueError, match="approved"):
        review.transition(case.repo, analysis.scan.scan_id, frozen_revision,
                           OWNER, "comment", "A late change must not mutate an approved record.")
    with pytest.raises(PermissionError, match="supervisor"):
        review.transition(case.repo, analysis.scan.scan_id, frozen_revision,
                           OWNER, "reopen", REASON)
    analysis = review.transition(case.repo, analysis.scan.scan_id, frozen_revision,
                                 APPROVER, "reopen", "Additional evidence requires this inspection to be reviewed again.")
    assert analysis.review.approved_at is None
    assert analysis.review.approved_by is None
    assert analysis.product_status == "NEEDS_REVIEW"
    assert case.repo.revision(analysis.scan.scan_id, frozen_revision).review.status == "approved"


def test_ocr_correction_preserves_original_record_and_relational_evidence(case):
    scan_id = case.a.scan.scan_id
    before = case.repo.revision(scan_id, 0).model_dump_json()
    old_quantity = case.a.declarations[DC.NET_QUANTITY]
    analysis = review.correct(case.repo, case.rules, scan_id, 0, OWNER,
                              DC.NET_QUANTITY, "Net quantity: 250 g", 0, old_quantity.bbox,
                              "The officer read 250 g on the source label and corrected the OCR value.")
    assert analysis.declarations[DC.NET_QUANTITY].norm["value_base"] == 250
    assert analysis.declarations[DC.NET_QUANTITY].norm["original_ocr"] == old_quantity.raw
    assert analysis.review.corrections[0]["before"]["raw"] == old_quantity.raw
    assert analysis.review.corrections[0]["actor_id"] == OWNER.id
    assert "net_quantity_cap_height" not in analysis.measurements
    assert "min_declaration_height" not in analysis.measurements
    assert case.repo.revision(scan_id, 0).model_dump_json() == before
    assert case.repo.get(scan_id).review.revision == 1
    with sqlite3.connect(case.repo.path) as conn:
        stored = conn.execute("SELECT revision,raw FROM extracted_declaration WHERE scan_id=? AND kind=? ORDER BY revision",
                              (scan_id, DC.NET_QUANTITY.value)).fetchall()
        assert stored == [(0, old_quantity.raw), (1, "Net quantity: 250 g")]
        assert conn.execute("SELECT COUNT(*) FROM product_image WHERE scan_id=?", (scan_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ocr_result WHERE scan_id=?", (scan_id,)).fetchone()[0] > 0


def test_stale_revision_never_overwrites_newer_officer_comment(case):
    analysis = review.transition(case.repo, case.a.scan.scan_id, 0, OWNER, "comment", "First officer observation.")
    with pytest.raises(RevisionConflict, match="another session"):
        review.transition(case.repo, analysis.scan.scan_id, 0, OWNER, "comment", "This stale edit must be rejected.")
    latest = case.repo.get(analysis.scan.scan_id)
    assert latest.review.revision == 1
    assert [c["text"] for c in latest.review.comments] == ["First officer observation."]
    assert len(case.repo.revisions(latest.scan.scan_id)) == 2


def test_simultaneous_edits_commit_one_revision_and_reject_the_other(case):
    barrier = threading.Barrier(2)

    def edit(text):
        barrier.wait(timeout=5)
        try:
            review.transition(case.repo, case.a.scan.scan_id, 0, OWNER, "comment", text)
            return "saved"
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, text) for text in (
            "Observation from first concurrent browser session.",
            "Observation from second concurrent browser session.")]
        assert sorted(future.result(timeout=10) for future in futures) == ["conflict", "saved"]
    latest = case.repo.get(case.a.scan.scan_id)
    assert latest.review.revision == 1
    assert len(latest.review.comments) == 1
    assert len(case.repo.revisions(latest.scan.scan_id)) == 2


def test_unauthorized_inspector_and_bad_reason_leave_no_revision(case):
    finding = case.a.findings[0]
    with pytest.raises(PermissionError, match="assigned inspector"):
        review.decide(case.repo, case.a.scan.scan_id, 0, OTHER, finding.finding_id, "PASS", REASON)
    with pytest.raises(ValueError, match="reason"):
        review.decide(case.repo, case.a.scan.scan_id, 0, OWNER, finding.finding_id, "PASS", "ok")
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0
    assert len(case.repo.revisions(case.a.scan.scan_id)) == 1


@pytest.mark.parametrize("damaged", ["image", "original"])
def test_missing_or_changed_evidence_blocks_decision_correction_and_approval(case, damaged):
    analysis = submit(case, resolved(case))
    if damaged == "image":
        case.image.unlink()
    else:
        case.original.write_bytes(b"Altered original evidence")
    before = analysis.review.revision
    with pytest.raises(ValueError, match="Evidence"):
        review.decide(case.repo, analysis.scan.scan_id, before, OWNER,
                       analysis.findings[0].finding_id, "PASS", REASON)
    declaration = analysis.declarations[DC.NET_QUANTITY]
    with pytest.raises(ValueError, match="Evidence"):
        review.correct(case.repo, case.rules, analysis.scan.scan_id, before, OWNER,
                        DC.NET_QUANTITY, declaration.raw, 0, declaration.bbox, REASON)
    with pytest.raises(ValueError, match="Evidence integrity"):
        review.transition(case.repo, analysis.scan.scan_id, before, APPROVER, "approve", REASON)
    assert case.repo.get(analysis.scan.scan_id).review.revision == before


@pytest.mark.parametrize("bbox", [[0, 0, 0, 10], [-1, 0, 40, 40], [0, 0, 1e9, 10], [0, 0, 1]])
def test_correction_requires_valid_source_rectangle(case, bbox):
    with pytest.raises(ValueError, match="rectangle"):
        review.correct(case.repo, case.rules, case.a.scan.scan_id, 0, OWNER,
                        DC.NET_QUANTITY, "Net quantity: 250 g", 0, bbox, REASON)
    assert case.repo.get(case.a.scan.scan_id).review.revision == 0


def test_correction_rejects_different_rule_version(case):
    mismatched = RulesEngine(case.rules.pack.model_copy(update={"version": "different-law-version"}))
    with pytest.raises(ValueError, match="exact original rule version"):
        review.correct(case.repo, mismatched, case.a.scan.scan_id, 0, OWNER,
                        DC.NET_QUANTITY, "Net quantity: 250 g", 0,
                        case.a.declarations[DC.NET_QUANTITY].bbox, REASON)


def test_failed_revision_transaction_rolls_back_mutated_record(case):
    def broken_change(analysis):
        analysis.scan.operator = "Uncommitted replacement"
        analysis.review.status = "approved"
        raise ValueError("Simulated validation failure after in-memory mutation")
    with pytest.raises(ValueError, match="Simulated"):
        case.repo.revise(case.a.scan.scan_id, 0, OWNER.id, "invalid.operation", REASON, broken_change)
    latest = case.repo.get(case.a.scan.scan_id)
    assert latest.scan.operator == OWNER.display_name
    assert latest.review.status == "draft"
    assert latest.review.revision == 0


def test_save_cannot_bypass_audited_revisions_and_get_returns_detached_record(case):
    detached = case.repo.get(case.a.scan.scan_id)
    detached.scan.operator = "Untracked edit"
    assert case.repo.get(case.a.scan.scan_id).scan.operator == OWNER.display_name
    with pytest.raises(ValueError, match="audited revision"):
        case.repo.save(detached)
    assert case.repo.save(case.repo.get(case.a.scan.scan_id)) == case.a.scan.scan_id
    assert len(case.repo.revisions(case.a.scan.scan_id)) == 1


def test_rule_version_archive_is_immutable_per_version(case):
    original = case.repo.archived_rules(case.rules.pack.version)
    changed = case.rules.pack.model_copy(deep=True)
    changed.rules = changed.rules[:-1]
    with pytest.raises(ValueError, match="new version"):
        case.repo.archive_rules(changed, actor_id=APPROVER.id)
    assert case.repo.archived_rules(case.rules.pack.version) == original


def test_existing_database_upgrade_retains_original_serialized_record(case):
    # Simulate the prior schema by creating only the original core tables.
    from tula.storage.db import SCHEMA
    legacy = case.directory / "legacy.db"
    raw = case.a.model_dump(mode="json")
    raw.pop("review")
    raw.pop("intelligence")
    raw["scan"].pop("inspector_id")
    original_json = json.dumps(raw, indent=2)
    with sqlite3.connect(legacy) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO inspection(scan_id,captured_at,record) VALUES (?,?,?)",
                     (case.a.scan.scan_id, case.a.scan.captured_at.isoformat(), original_json))
    upgraded = Repository(legacy)
    Repository(legacy)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT record FROM inspection").fetchone()[0] == original_json
        assert conn.execute("SELECT COUNT(*) FROM inspection_revision").fetchone()[0] == 1
    assert upgraded.get(case.a.scan.scan_id).review.revision == 0


@pytest.fixture
def http(case):
    web = SimpleNamespace(app=FastAPI(), repo=case.repo, rules=case.rules,
                          UPLOADS=case.directory / "uploads")
    store = install_security(web.app, case.repo.path)
    admin = store.create_user("workflow-admin", "A real isolated test password", role="admin", bootstrap=True)
    login = store.login(admin.username, "A real isolated test password")
    install_workflow(web)
    with TestClient(web.app, base_url="https://testserver") as client:
        client.cookies.set("tula_session", login.token)
        client.headers["X-CSRF-Token"] = login.session.csrf_token
        yield client, store, web
    if getattr(web.app.state, "jobs", None):
        web.app.state.jobs.stop()


def upload_bytes():
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_rescan_rejects_changed_parent_before_accepting_new_upload(case, http):
    client, _, web = http
    case.image.write_bytes(b"Tampered parent image")
    response = client.post("/v1/inspections", data={"parent_scan_id": case.a.scan.scan_id},
                           files={"files": ("close-up.png", upload_bytes(), "image/png")})
    assert response.status_code == 409
    assert "missing or changed" in response.json()["detail"]
    assert not web.UPLOADS.exists()


def test_allergen_missing_record_and_stale_revision_are_correct_http_errors(case, http):
    client, _, _ = http
    response = client.post("/inspections/missing/allergens", data={"revision": 0, "concerns": "milk"})
    assert response.status_code == 404
    review.transition(case.repo, case.a.scan.scan_id, 0, OWNER, "comment", "Revision changed by another officer.")
    response = client.post(f"/inspections/{case.a.scan.scan_id}/allergens",
                           data={"revision": 0, "concerns": "milk"})
    assert response.status_code == 409


def test_backend_rejects_other_inspectors_rescan(case, http):
    client, store, _ = http
    administrator = store.list_users()[0]
    outsider = store.create_user("other-inspector", "A real isolated test password", actor_id=administrator.id)
    login = store.login(outsider.username, "A real isolated test password")
    client.cookies.clear()
    client.cookies.set("tula_session", login.token)
    client.headers["X-CSRF-Token"] = login.session.csrf_token
    response = client.post("/v1/inspections", data={"parent_scan_id": case.a.scan.scan_id},
                           files={"files": ("close-up.png", upload_bytes(), "image/png")})
    assert response.status_code == 403


def test_historic_revision_endpoint_preserves_machine_observation(case, http):
    client, _, _ = http
    before = client.get(f"/inspections/{case.a.scan.scan_id}/revisions/0").json()
    review.correct(case.repo, case.rules, case.a.scan.scan_id, 0, OWNER, DC.NET_QUANTITY,
                    "Net quantity: 250 g", 0, case.a.declarations[DC.NET_QUANTITY].bbox, REASON)
    response = client.get(f"/inspections/{case.a.scan.scan_id}/revisions/0")
    assert response.status_code == 200
    assert response.json() == before
    latest = client.get(f"/inspections/{case.a.scan.scan_id}/revisions/1").json()
    assert latest["declarations"][DC.NET_QUANTITY.value]["norm"]["value_base"] == 250
