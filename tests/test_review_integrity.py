"""Approval independence survives overwritten facts and decisions."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageDraw

from tula.domain.enums import DeclarationClass as DC
from tula.domain.models import (
    Analysis,
    Declaration,
    EvidenceCoverage,
    PackageFacts,
    Scan,
    sha256_file,
)
from tula.rules.engine import RulesEngine
from tula.rules.spec import Rule, RulePack
from tula.security import User
from tula.services import review
from tula.storage.db import Repository
from tula.storage.workflow import RevisionConflict

OWNER = User("owner", "owner", "Assigned inspector", "inspector", True)
OUTSIDER = User("outsider", "outsider", "Unassigned inspector", "inspector", True)
EDITOR = User("editor", "editor", "Reviewing supervisor", "supervisor", True)
APPROVER = User("approver", "approver", "Independent supervisor", "supervisor", True)
ADMIN = User("admin", "admin", "Administrator", "admin", True)
REASON = "Compared the retained package image with the recorded declaration."
FACTS = {"category": "general", "category_confirmed": True, "bundle_type": "single",
         "bundle_confirmed": True, "shape": "rectangular", "shape_confirmed": True,
         "is_imported": False, "imported_confirmed": True}


@pytest.fixture
def case(tmp_path):
    frame = tmp_path / "working.png"
    image = Image.new("RGB", (240, 80), "white")
    ImageDraw.Draw(image).text((10, 10), "Net quantity 200 g", fill="black")
    image.save(frame)
    original = tmp_path / "original.png"
    original.write_bytes(frame.read_bytes())
    rule = Rule(id="TEST.NET", title="Quantity presence", citation={"clause": "Test source"},
        effective_from=date(2020, 1, 1), declaration=DC.NET_QUANTITY,
        assertion={"present": "$decl.net_quantity"})
    rules = RulesEngine(RulePack(version="review-integrity-fixture-v1", title="Test fixture", rules=[rule]))
    scan = Scan(scan_id="REVIEW-INTEGRITY", inspector_id=OWNER.id, frames=[str(frame)],
        frame_hashes={str(frame): sha256_file(str(frame))},
        original_frame_hashes={str(original): sha256_file(str(original))},
        coverage=EvidenceCoverage.assumed_complete(), source="bench")
    declaration = Declaration(klass=DC.NET_QUANTITY, raw="Net quantity 200 g", frame=str(frame),
        bbox=(10, 10, 220, 45), norm={"value_base": 200, "unit_base": "g"})
    a = Analysis(scan=scan, package=PackageFacts(), declarations={DC.NET_QUANTITY: declaration},
                 rules_version=rules.pack.version)
    a.findings = rules.evaluate_all(scan, a.package, a.declarations, {})
    repo = Repository(tmp_path / "review.db")
    repo.archive_rules(rules.pack)
    repo.save(a)
    return SimpleNamespace(repo=repo, a=a, rules=rules, frame=frame, original=original)


def current(case):
    return case.repo.get(case.a.scan.scan_id)


def act(case, actor, action, reason=REASON, revision=None):
    a = current(case)
    rev = a.review.revision if revision is None else revision
    if action == "decide":
        return review.decide(case.repo, a.scan.scan_id, rev, actor, a.findings[0].finding_id, "PASS", reason)
    if action == "context":
        return review.confirm_context(case.repo, case.rules, a.scan.scan_id, rev, actor, FACTS, reason)
    if action == "correct":
        return review.correct(case.repo, case.rules, a.scan.scan_id, rev, actor,
                              DC.NET_QUANTITY, "Net quantity 200 g", 0, (10, 10, 220, 45), reason)
    return review.transition(case.repo, a.scan.scan_id, rev, actor, action, reason)


@pytest.mark.parametrize("material", ["context", "correct", "decide"])
def test_material_author_cannot_approve_after_another_officer_replaces_work(case, material):
    act(case, EDITOR, material)
    act(case, OWNER, material)
    act(case, OWNER, "submit")
    before = current(case)
    with pytest.raises(PermissionError, match="did not correct or decide"):
        act(case, EDITOR, "approve")
    assert current(case) == before
    assert act(case, APPROVER, "approve").review.status == "approved"


@pytest.mark.parametrize("replacement", ["context", "correct"])
def test_decision_history_survives_rechecks_that_clear_decisions(case, replacement):
    act(case, EDITOR, "decide")
    act(case, OWNER, replacement)
    assert current(case).review.decisions == {}
    act(case, OWNER, "submit")
    with pytest.raises(PermissionError, match="did not correct or decide"):
        act(case, EDITOR, "approve")


def test_legacy_correction_history_still_counts_when_current_list_is_replaced(case):
    act(case, EDITOR, "correct")
    a = current(case)
    case.repo.revise(a.scan.scan_id, a.review.revision, OWNER.id, "legacy.replaced", REASON,
                    lambda a: a.review.corrections.clear())
    act(case, OWNER, "submit")
    with pytest.raises(PermissionError, match="did not correct or decide"):
        act(case, EDITOR, "approve")


def test_supplementary_label_editor_cannot_approve_after_observation_is_replaced(case):
    from tula.services.intelligence_review import correct_observation

    for actor in (EDITOR, OWNER):
        a = current(case)
        correct_observation(case.repo, a.scan.scan_id, a.review.revision, actor,
                            "batch_number", "Batch No. ABC123", 0, (10, 10, 220, 45), REASON,
                            observation_index=-1 if actor == EDITOR else 0)
    act(case, OWNER, "submit")
    with pytest.raises(PermissionError, match="did not correct or decide"):
        act(case, EDITOR, "approve")
    assert act(case, APPROVER, "approve").review.status == "approved"


def test_approval_fails_closed_when_a_retained_revision_cannot_be_read(case, monkeypatch):
    act(case, OWNER, "comment")
    act(case, OWNER, "submit")
    before = current(case)
    revision = case.repo.revision
    monkeypatch.setattr(case.repo, "revision", lambda scan_id, number:
                        None if number == 1 else revision(scan_id, number))
    with pytest.raises(ValueError, match="Complete review history"):
        act(case, APPROVER, "approve")
    assert current(case) == before


@pytest.mark.parametrize("participation", ["comment", "request_rescan"])
def test_comment_or_rescan_only_supervisor_can_still_approve(case, participation):
    act(case, EDITOR, participation)
    act(case, OWNER, "submit")
    assert act(case, EDITOR, "approve").review.status == "approved"


def test_reopening_without_editing_facts_does_not_disqualify_supervisor(case):
    act(case, OWNER, "submit")
    act(case, APPROVER, "approve")
    act(case, EDITOR, "reopen")
    act(case, OWNER, "submit")
    assert act(case, EDITOR, "approve").review.status == "approved"


@pytest.mark.parametrize("action", ["decide", "correct", "context", "submit"])
@pytest.mark.parametrize("damage", ["changed", "missing_original"])
def test_material_actions_require_intact_working_and_original_evidence(case, action, damage):
    if damage == "changed":
        case.frame.write_bytes(b"changed fixture image")
    else:
        case.original.unlink()
    before = current(case)
    with pytest.raises(ValueError, match="Evidence"):
        act(case, OWNER, action)
    assert current(case) == before


@pytest.mark.parametrize("action", ["decide", "context", "submit", "approve"])
def test_empty_image_list_is_not_verified_evidence(case, action):
    def remove_images(a):
        a.scan.frames = []
        a.scan.frame_hashes = {}
        a.scan.original_frame_hashes = {}
        if action == "approve":
            a.review.status, a.review.submitted_by = "submitted", OWNER.id
    case.repo.revise(case.a.scan.scan_id, 0, OWNER.id, "test.empty_evidence", REASON, remove_images)
    before = current(case)
    with pytest.raises(ValueError, match="No source images"):
        act(case, APPROVER if action == "approve" else OWNER, action)
    assert current(case) == before


def test_reopen_and_rescan_request_can_record_remedy_for_damaged_evidence(case):
    act(case, OWNER, "submit")
    act(case, APPROVER, "approve")
    case.frame.write_bytes(b"changed after approval")
    assert act(case, EDITOR, "reopen").review.status == "in_review"
    assert act(case, OWNER, "request_rescan").review.comments[-1]["action"] == "request_rescan"


@pytest.mark.parametrize("action", ["decide", "correct", "context", "submit", "comment", "request_rescan"])
def test_unassigned_inspector_cannot_change_any_review_surface(case, action):
    before = current(case)
    with pytest.raises(PermissionError, match="assigned inspector"):
        act(case, OUTSIDER, action)
    assert current(case) == before


@pytest.mark.parametrize("action", ["decide", "correct", "context", "submit", "comment", "request_rescan"])
def test_approved_record_is_locked_on_all_edit_surfaces(case, action):
    act(case, OWNER, "submit")
    act(case, APPROVER, "approve")
    before = current(case)
    with pytest.raises(ValueError, match="approved"):
        act(case, OWNER, action)
    assert current(case) == before


def test_only_approved_records_can_be_reopened(case):
    with pytest.raises(ValueError, match="Only an approved"):
        act(case, EDITOR, "reopen")
    assert current(case).review.revision == 0


@pytest.mark.parametrize("reason", [None, True, "     ", ".....", "00000", "aaaaa", "ok"])
def test_reason_cannot_be_blank_punctuation_or_placeholder_only(case, reason):
    with pytest.raises(ValueError, match="meaningful written reason"):
        act(case, OWNER, "comment", reason=reason)
    assert current(case).review.revision == 0


@pytest.mark.parametrize("action", ["decide", "correct", "context", "submit"])
def test_stale_material_changes_preserve_latest_revision(case, action):
    act(case, OWNER, "comment")
    with pytest.raises(RevisionConflict):
        act(case, OWNER, action, revision=0)
    assert current(case).review.revision == 1


@pytest.mark.parametrize("actor", [User("owner", "owner", "Disabled", "inspector", False),
                                  SimpleNamespace(id="owner", role="viewer", display_name="Invalid role")])
def test_inactive_or_unknown_role_cannot_edit_even_when_id_matches_owner(case, actor):
    with pytest.raises(PermissionError, match="active inspector"):
        act(case, actor, "decide")


def test_context_preserves_original_rule_date_and_scope_evidence(case):
    def seed(a):
        a.package.legal_context = {"assessment_date": "2022-03-01", "assessment_date_confirmed": True,
                                   "scope_evidence_text": "Preserved original label context"}
    case.repo.revise(case.a.scan.scan_id, 0, OWNER.id, "test.context", REASON, seed)
    before = current(case)
    after = act(case, EDITOR, "context")
    assert after.rules_version == before.rules_version
    assert after.package.legal_context["assessment_date"] == "2022-03-01"
    assert after.package.legal_context["scope_evidence_text"] == "Preserved original label context"
    assert case.repo.revision(case.a.scan.scan_id, 1) == before


@pytest.mark.parametrize("actor,editable", [(OUTSIDER, False), (OWNER, True), (EDITOR, True), (ADMIN, True)])
def test_review_and_context_templates_match_assignment_permissions(case, actor, editable):
    html = render_review(case, actor)
    assert ("Save correction &amp; recheck" in html or "Save correction & recheck" in html) is editable
    assert ("Save package facts &amp; recheck" in html or "Save package facts & recheck" in html) is editable
    assert ('name="action" value="submit"' in html) is editable
    assert ("Capture a targeted close-up" in html) is editable
    if not editable:
        assert "Read-only access" in html
        assert "data-review-form" not in html


def render_review(case, actor):
    directory = Path(__file__).resolve().parents[1] / "src/tula/web/templates"
    env = Environment(loader=FileSystemLoader(directory), autoescape=select_autoescape())
    env.globals["asset_url"] = lambda name: "/static/" + name
    return env.get_template("_review.html").render(a=current(case),
        request=SimpleNamespace(state=SimpleNamespace(user=actor)), csrf_token="test-token",
        revisions=case.repo.revisions(case.a.scan.scan_id), declaration_labels={DC.NET_QUANTITY: "Net quantity"})


def test_submitted_template_does_not_offer_duplicate_submission(case):
    act(case, OWNER, "submit")
    html = render_review(case, EDITOR)
    assert 'name="action" value="submit"' not in html
    assert 'name="action" value="approve"' in html


@pytest.mark.parametrize("actor,can_reopen", [(OUTSIDER, False), (OWNER, False), (EDITOR, True), (ADMIN, True)])
def test_approved_templates_offer_only_authorized_reopening(case, actor, can_reopen):
    act(case, OWNER, "submit")
    act(case, APPROVER, "approve")
    html = render_review(case, actor)
    assert 'id="correction-form"' not in html
    assert 'action="/inspections/REVIEW-INTEGRITY/context"' not in html
    assert ('name="action" value="reopen"' in html) is can_reopen
    for action in ("submit", "approve", "comment", "request_rescan"):
        assert f'name="action" value="{action}"' not in html
