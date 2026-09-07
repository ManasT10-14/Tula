"""Context attestation changes applicability without rewriting source history."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from PIL import Image

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Verdict
from tula.domain.models import (
    Analysis,
    Declaration,
    PackageFacts,
    ReviewDecision,
    Scan,
    sha256_file,
)
from tula.rules.engine import RulesEngine
from tula.services.context import validate_context
from tula.services.review import confirm_context
from tula.storage.db import Repository
from tula.storage.workflow import RevisionConflict

FACTS = {"category": "general", "category_confirmed": True,
         "bundle_type": "single", "bundle_confirmed": True,
         "shape": "rectangular", "shape_confirmed": True,
         "is_imported": False, "imported_confirmed": True}
OFFICER = SimpleNamespace(id="inspector", role="inspector", display_name="Test Inspector")


@pytest.fixture
def record(tmp_path):
    frame = tmp_path / "evidence.png"
    Image.new("RGB", (120, 160), "white").save(frame)
    rules = RulesEngine.from_directory()
    a = Analysis(scan=Scan(scan_id="CONTEXT", inspector_id=OFFICER.id,
        captured_at=datetime(2026, 9, 6, 21, 30, tzinfo=UTC), frames=[str(frame)],
        frame_hashes={str(frame): sha256_file(str(frame))}),
        package=PackageFacts(), declarations={DC.NET_QUANTITY: Declaration(
            klass=DC.NET_QUANTITY, raw="Net weight 200 g", norm={"value_base": 200, "unit_base": "g"})},
        rules_version=rules.pack.version)
    a.findings = rules.evaluate_all(a.scan, a.package, a.declarations, {})
    a.review.decisions[a.findings[0].finding_id] = ReviewDecision(
        finding_id=a.findings[0].finding_id, verdict=Verdict.PASS,
        actor_id=OFFICER.id, actor_name=OFFICER.display_name, reason="Previous recorded decision")
    a.review.status = "submitted"
    repo = Repository(tmp_path / "context.db")
    repo.archive_rules(rules.pack)
    repo.save(a)
    return repo, rules, a, frame


@pytest.mark.parametrize("raw", [[], None, {"category": []}, {"shape": 3},
    {"category_confirmed": "true"}, {"category": "unknown", "category_confirmed": True},
    {"imported_confirmed": True}, {"assessment_date": "2020-01-01"},
    {"is_imported": 1}])
def test_untrusted_context_does_not_inject_types_or_assessment_dates(raw):
    with pytest.raises((TypeError, ValueError)):
        validate_context(raw, actor_id=OFFICER.id)


def test_context_reruns_rules_preserving_original_revision_and_assessment_date(record):
    repo, rules, original, _ = record
    updated = confirm_context(repo, rules, "CONTEXT", 0, OFFICER, FACTS,
                              "Verified the package type, origin and physical shape.")
    assert updated.review.revision == 1
    assert updated.review.status == "in_review" and not updated.review.decisions
    assert updated.package.legal_context["assessment_date"] == "2026-09-07"
    assert updated.package.legal_context["attested_by"] == OFFICER.id
    assert updated.package.legal_context["category_confirmed"] is True
    assert updated.package.is_imported is False
    assert repo.revision("CONTEXT", 0) == original
    assert repo.revisions("CONTEXT")[0]["action"] == "package.context_confirmed"
    assert updated.review.comments[-1]["before"] == {}
    assert updated.review.comments[-1]["after"]["category"] == "general"
    assert any(f.rule_id.endswith("UNIT_PRICE_PRESENT") for f in updated.findings)


def test_stale_context_change_does_not_overwrite_a_newer_attestation(record):
    repo, rules, _, _ = record
    confirm_context(repo, rules, "CONTEXT", 0, OFFICER, FACTS, "Verified package facts.")
    with pytest.raises(RevisionConflict):
        confirm_context(repo, rules, "CONTEXT", 0, OFFICER, {}, "Attempt with obsolete page.")
    assert repo.get("CONTEXT").package.legal_context["category"] == "general"
    assert len(repo.revisions("CONTEXT")) == 2


@pytest.mark.parametrize("failure", ["owner", "approved", "evidence", "invalid_context", "version"])
def test_context_failures_leave_record_and_history_unchanged(record, failure):
    repo, rules, _original, frame = record
    actor, facts = OFFICER, FACTS
    if failure == "owner":
        actor = SimpleNamespace(id="other", role="inspector", display_name="Other")
    elif failure == "approved":
        repo.revise("CONTEXT", 0, "supervisor", "test.approved", "Prepared approved state",
                    lambda a: setattr(a.review, "status", "approved"))
    elif failure == "evidence":
        frame.write_bytes(b"changed")
    elif failure == "invalid_context":
        facts = {"category": "made-up"}
    else:
        rules = RulesEngine(rules.pack.model_copy(update={"version": "different"}))
    before = repo.get("CONTEXT")
    with pytest.raises((ValueError, PermissionError)):
        confirm_context(repo, rules, "CONTEXT", before.review.revision, actor, facts,
                        "Facts require a fresh evidence review.")
    assert repo.get("CONTEXT") == before
    assert len(repo.revisions("CONTEXT")) == before.review.revision + 1


def test_server_assessment_date_can_be_retained_without_trusting_client_date():
    result = validate_context(FACTS, actor_id=OFFICER.id, assessed_on=date(2025, 4, 2))
    assert result["assessment_date"] == "2025-04-02"
