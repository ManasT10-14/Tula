"""Linked captures remain separate records and keep navigable provenance."""
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tula.domain.models import Analysis, PackageFacts, Scan
from tula.report import render
from tula.services.rescan_history import linked_captures
from tula.storage.db import Repository


def record(name, *, parent=None, minute=0, officer="Inspector"):
    return Analysis(scan=Scan(scan_id=name, parent_scan_id=parent, operator=officer,
        captured_at=datetime(2026, 9, 7, tzinfo=UTC) + timedelta(minutes=minute)),
        package=PackageFacts(), rules_version="draft-test")


def test_parent_and_child_navigation_does_not_combine_review_states(tmp_path):
    repo = Repository(tmp_path / "case.db")
    parent = record("PARENT")
    parent.review.status = "approved"
    repo.save(parent)
    first, second = record("FIRST", parent="PARENT", minute=1), record("SECOND", parent="PARENT", minute=2)
    repo.save(first)
    repo.save(second)
    repo.save(record("UNRELATED", minute=3))
    before = parent.model_dump_json()
    result = linked_captures(repo, parent, limit=1)
    assert result["total_children"] == 2
    assert [row["scan_id"] for row in result["children"]] == ["SECOND"]
    assert result["children"][0]["review_status"] == "draft"
    assert result["parent"] is None
    child = linked_captures(repo, first)
    assert child["parent"]["scan_id"] == "PARENT"
    assert child["parent"]["review_status"] == "approved"
    assert child["children"] == []
    assert repo.get("PARENT").model_dump_json() == before


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "10"])
def test_link_query_bounds_are_validated_before_database_access(limit):
    with pytest.raises(ValueError):
        linked_captures(None, record("PARENT"), limit=limit)


def test_missing_parent_summary_still_keeps_recorded_reference(tmp_path):
    repo = Repository(tmp_path / "case.db")
    a = record("CHILD", parent="HISTORICAL")
    result = linked_captures(repo, a)
    assert result == {"parent": None, "children": [], "total_children": 0, "limit": 20,
                      "snapshot": None, "snapshot_status": "not_recorded"}
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "src/tula/web/templates"),
                      autoescape=select_autoescape())
    page = env.get_template("_rescan_history.html").render(a=a, linked_captures=result)
    assert '/inspections/HISTORICAL' in page
    assert "current summary is unavailable" in page


def test_history_template_escapes_officer_and_rule_values():
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "src/tula/web/templates"),
                      autoescape=select_autoescape())
    data = {"parent": None, "children": [{"scan_id": "CHILD", "operator": "<script>bad()</script>",
        "captured_at": "2026-09-07", "review_status": "draft", "revision": 0,
        "product_status": "NEEDS_REVIEW", "rules_version": "<b>draft</b>"}], "total_children": 2}
    page = env.get_template("_rescan_history.html").render(a=record("PARENT"), linked_captures=data)
    assert "&lt;script&gt;" in page and "<script>bad" not in page
    assert "&lt;b&gt;draft&lt;/b&gt;" in page
    assert 'aria-label="Linked rescan inspections"' in page
    assert "showing the 1 most recent" in page


def test_export_scope_keeps_parent_reference_without_transferring_approval():
    a = record("CHILD", parent="PARENT")
    note = render.scope_note(a)
    assert "linked rescan of PARENT" in note
    assert "Approval of either record does not approve the other" in note
    assert a.review.status == "draft"
    assert "linked rescan" not in render.scope_note(record("STANDALONE"))


def test_captured_revision_shows_earlier_assertions_after_parent_changes(tmp_path):
    repo = Repository(tmp_path / "case.db")
    parent = record("PARENT")
    parent.review.corrections = [{"kind": "net_quantity", "after": {"raw": "Net wt 500 g"},
                                  "actor": "Earlier officer", "reason": "Original inspected value"}]
    repo.save(parent)
    with repo._connect() as conn:
        raw = conn.execute("SELECT record FROM inspection_revision WHERE scan_id='PARENT' AND revision=0").fetchone()[0]
    child = record("CHILD", parent="PARENT")
    child.scan.parent_revision = 0
    child.scan.parent_record_sha256 = hashlib.sha256(raw.encode()).hexdigest()
    def change(a):
        a.review.corrections.append({"kind": "net_quantity", "after": {"raw": "Net wt 5 g"},
                                    "actor": "Later officer", "reason": "Later revision"})
    repo.revise("PARENT", 0, "officer", "corrected", "Later review", change)
    result = linked_captures(repo, child)
    assert result["parent"]["revision"] == 1
    assert result["snapshot"]["revision"] == 0
    assert result["snapshot"]["correction_count"] == 1
    assert result["snapshot"]["corrections"][0]["raw"] == "Net wt 500 g"
    assert child.review.corrections == [] and child.review.status == "draft"
    child.scan.parent_record_sha256 = "0" * 64
    mismatched = linked_captures(repo, child)
    assert mismatched["snapshot"] is None and mismatched["snapshot_status"] == "unavailable"


def test_reports_include_recorded_rescan_target_revision_and_hash():
    a = record("CHILD", parent="PARENT")
    a.scan.parent_revision = 2
    a.scan.parent_record_sha256 = "a" * 64
    a.scan.rescan_target = "expiry_date"
    rows = next(section.rows for section in render.build(a) if section.title == "Original inspection reference")
    assert dict(rows) == {"Original inspection": "PARENT", "Original revision used": "2",
                          "Original record SHA-256": "a" * 64, "Close-up focus": "Expiry date"}
