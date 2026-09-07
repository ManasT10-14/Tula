"""Queryable product/rule/finding/evidence history and additive migrations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

import pytest
from PIL import Image

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Verdict
from tula.domain.models import (
    Analysis,
    Citation,
    Declaration,
    EvidenceCoverage,
    Finding,
    PackageFacts,
    ReviewDecision,
    Scan,
    sha256_file,
)
from tula.forensics.gtin import check_digit
from tula.rules.spec import Rule, RulePack
from tula.storage.catalog import index_analysis, index_rule_pack, upgrade_catalog
from tula.storage.db import SCHEMA, Repository

RID = "TEST.NET_QUANTITY"
VERSION = "catalog-test-v1"


@pytest.fixture
def pack():
    return RulePack(version=VERSION, title="Explicit catalog test rules", rules=[Rule(
        id=RID, title="Net quantity", citation={"clause": "Test clause", "text": "Test-only source"},
        effective_from=date(2020, 1, 1), declaration=DC.NET_QUANTITY,
        sources=[{"url": "https://example.com/test-source", "notification": "TEST", "publication_date": "2020-01-01", "clause": "Test", "review_status": "test fixture"}])])


@pytest.fixture
def analysis(tmp_path):
    frame = tmp_path / "source.png"
    Image.new("RGB", (80, 40), "white").save(frame)
    path = str(frame)
    body = "890123456789"
    gtin = body + str(check_digit(body))
    decl = Declaration(klass=DC.NET_QUANTITY, raw="Net quantity 200 g", frame=path,
        bbox=(1, 2, 40, 20), confidence=0.95,
        norm={"value_base": 200, "unit_base": "g", "source_spans": [
            {"frame": path, "bbox": [2, 3, 30, 15], "text": "200 g", "ocr_confidence": 0.94}]})
    finding = Finding(finding_id="F-1", rule_id=RID, rules_version=VERSION,
        citation=Citation(clause="Test clause", text="Test-only source"),
        declaration=DC.NET_QUANTITY, verdict=Verdict.PASS, evidence=["net_quantity", "consumer_care"])
    return Analysis(scan=Scan(scan_id="CAT-1", frames=[path], frame_hashes={path: sha256_file(path)},
        coverage=EvidenceCoverage.assumed_complete(), source="bench"),
        package=PackageFacts(gtin=gtin), declarations={DC.NET_QUANTITY: decl},
        findings=[finding], rules_version=VERSION)


def query(repo, sql, args=()):
    with repo._connect() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def retain(conn, a, *, current=True):
    """Pre-catalog fixture writes exact source bytes, not derived catalog rows."""
    text = a.model_dump_json(indent=2)
    if current:
        conn.execute("INSERT INTO inspection(scan_id,captured_at,record) VALUES (?,?,?) ON CONFLICT(scan_id) DO UPDATE SET record=excluded.record",
                     (a.scan.scan_id, a.scan.captured_at.isoformat(), text))
    conn.execute("INSERT INTO inspection_revision VALUES (?,?,?,?,?,?,?)", (
        a.scan.scan_id, a.review.revision, a.scan.captured_at.isoformat(), None,
        "test.retained", "Explicit test source snapshot", text))
    return text


def test_normal_repository_hooks_link_product_rule_and_evidence(tmp_path, analysis, pack):
    repo = Repository(tmp_path / "normal.db")
    repo.archive_rules(pack)
    repo.save(analysis)
    rows = query(repo, """SELECT p.identity_kind,r.title,f.machine_verdict,e.snippet,i.path
        FROM catalog_product p JOIN inspection_product_revision x USING(product_id)
        JOIN compliance_finding f ON f.scan_id=x.scan_id AND f.revision=x.revision
        JOIN catalog_rule_definition r ON r.version=f.definition_version AND r.rule_id=f.definition_rule_id
        JOIN finding_evidence e ON e.scan_id=f.scan_id AND e.revision=f.revision AND e.finding_id=f.finding_id
        JOIN product_image i ON i.scan_id=e.scan_id AND i.frame_index=e.frame_index
        WHERE e.availability='linked'""")
    assert len(rows) == 2
    assert rows[0]["identity_kind"] == "gtin"
    assert {row["snippet"] for row in rows} == {"Net quantity 200 g", "200 g"}
    assert query(repo, "SELECT url FROM catalog_rule_source")[0]["url"].endswith("test-source")
    assert query(repo, "PRAGMA foreign_key_check") == []


def test_missing_declaration_evidence_is_explicit(tmp_path, analysis):
    repo = Repository(tmp_path / "missing.db")
    repo.save(analysis)
    evidence = query(repo, "SELECT * FROM finding_evidence WHERE evidence_kind='consumer_care'")[0]
    assert evidence["availability"] == "declaration_missing"
    assert evidence["frame_index"] is None and evidence["declaration_kind"] is None
    assert evidence["coverage_complete"] == 1


def test_unknown_source_frame_does_not_borrow_an_image(tmp_path, analysis):
    repo = Repository(tmp_path / "frame.db")
    analysis.declarations[DC.NET_QUANTITY].frame = "unretained-photo.png"
    analysis.declarations[DC.NET_QUANTITY].norm["source_spans"] = []
    repo.save(analysis)
    evidence = query(repo, "SELECT * FROM finding_evidence WHERE evidence_kind='net_quantity'")[0]
    assert evidence["availability"] == "unknown_frame"
    assert evidence["frame_index"] is None
    assert evidence["reported_frame"] == "unretained-photo.png"


def test_valid_gtin_links_inspections_but_invalid_values_do_not(tmp_path, analysis):
    repo = Repository(tmp_path / "identities.db")
    repo.save(analysis)
    second = analysis.model_copy(deep=True)
    second.scan.scan_id = "CAT-2"
    repo.save(second)
    linked = query(repo, "SELECT product_id FROM inspection_product_revision ORDER BY scan_id")
    assert linked[0] == linked[1]
    for name in ("BAD-1", "BAD-2"):
        bad = analysis.model_copy(deep=True)
        bad.scan.scan_id = name
        bad.package.gtin = "invalid-same-code"
        repo.save(bad)
    unlinked = query(repo, "SELECT product_id FROM inspection_product_revision WHERE scan_id LIKE 'BAD-%'")
    assert unlinked[0] != unlinked[1]


def test_restricted_store_barcode_is_not_global_product_identity(tmp_path, analysis):
    repo = Repository(tmp_path / "restricted.db")
    body = "201234567890"
    analysis.package.gtin = body + str(check_digit(body))
    repo.save(analysis)
    assert query(repo, "SELECT identity_kind FROM catalog_product")[0]["identity_kind"] == "inspection"


def test_revisions_keep_machine_and_human_outcomes_separate(tmp_path, analysis, pack):
    repo = Repository(tmp_path / "revisions.db")
    repo.archive_rules(pack)
    repo.save(analysis)
    def change(a):
        a.review.decisions["F-1"] = ReviewDecision(finding_id="F-1", verdict=Verdict.VIOLATION,
            reason="Inspected original source", actor_id="officer", actor_name="Test officer")
        a.declarations[DC.NET_QUANTITY].raw = "Net quantity 100 g"
        a.declarations[DC.NET_QUANTITY].norm["value_base"] = 100
    repo.revise(analysis.scan.scan_id, 0, "officer", "test.correction", "Test revision", change)
    rows = query(repo, "SELECT revision,machine_verdict,reviewed_verdict,reviewer_id FROM compliance_finding ORDER BY revision")
    assert rows[0]["machine_verdict"] == rows[1]["machine_verdict"] == "PASS"
    assert rows[0]["reviewed_verdict"] is None
    assert rows[1]["reviewed_verdict"] == "VIOLATION" and rows[1]["reviewer_id"] == "officer"
    assert [r["net_quantity"] for r in query(repo, "SELECT net_quantity FROM inspection_product_revision ORDER BY revision")] == [200, 100]
    assert query(repo, "PRAGMA foreign_key_check") == []


def test_missing_historical_rule_is_not_fabricated_and_can_be_resolved(tmp_path, analysis, pack):
    repo = Repository(tmp_path / "versions.db")
    repo.save(analysis)
    missing = query(repo, "SELECT rule_link_status,definition_version FROM compliance_finding")[0]
    assert missing == {"rule_link_status": "missing_version", "definition_version": None}
    assert query(repo, "SELECT * FROM catalog_rule_definition") == []
    repo.archive_rules(pack)
    assert query(repo, "SELECT rule_link_status FROM compliance_finding")[0]["rule_link_status"] == "resolved"


def test_existing_version_without_rule_has_missing_rule_status(tmp_path, analysis, pack):
    repo = Repository(tmp_path / "missing-rule.db")
    pack.rules = []
    repo.archive_rules(pack)
    repo.save(analysis)
    assert query(repo, "SELECT rule_link_status FROM compliance_finding")[0]["rule_link_status"] == "missing_rule"


def test_catalog_rejects_rewriting_existing_revision(tmp_path, analysis):
    repo = Repository(tmp_path / "immutable.db")
    repo.save(analysis)
    analysis.declarations[DC.NET_QUANTITY].raw = "Changed without a revision"
    with repo._connect() as conn, pytest.raises(ValueError, match="cannot be overwritten"):
        index_analysis(conn, analysis)
    assert query(repo, "SELECT snippet FROM finding_evidence WHERE evidence_index=0")[0]["snippet"] == "Net quantity 200 g"


def test_catalog_rejects_index_content_different_from_retained_source(tmp_path, analysis):
    repo = Repository(tmp_path / "different.db")
    with repo._connect() as conn:
        retain(conn, analysis)
        analysis.declarations[DC.NET_QUANTITY].raw = "Unretained replacement"
        with pytest.raises(ValueError, match="differs from the retained"):
            index_analysis(conn, analysis)
    assert query(repo, "SELECT * FROM inspection_product_revision") == []


def test_failed_catalog_index_rolls_back_partial_relations(tmp_path, analysis):
    repo = Repository(tmp_path / "partial.db")
    analysis.findings.append(analysis.findings[0])
    with repo._connect() as conn:
        retain(conn, analysis)
        with pytest.raises(sqlite3.IntegrityError):
            index_analysis(conn, analysis)
        assert conn.execute("SELECT COUNT(*) FROM inspection_product_revision").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM catalog_product").fetchone()[0] == 0


def test_catalog_never_commits_its_callers_transaction(tmp_path, analysis):
    repo = Repository(tmp_path / "rollback.db")
    with repo._connect() as conn:
        conn.execute("BEGIN")
        retain(conn, analysis)
        index_analysis(conn, analysis)
        conn.rollback()
    assert query(repo, "SELECT * FROM inspection") == []
    assert query(repo, "SELECT * FROM compliance_finding") == []


def test_new_repository_migrates_original_current_json_without_rewriting(tmp_path, analysis):
    path = tmp_path / "old-current.db"
    payload = analysis.model_dump_json(indent=4)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO inspection(scan_id,captured_at,record) VALUES (?,?,?)",
                     (analysis.scan.scan_id, analysis.scan.captured_at.isoformat(), payload))
    repo = Repository(path)
    assert query(repo, "SELECT record FROM inspection")[0]["record"] == payload
    assert len(query(repo, "SELECT * FROM compliance_finding")) == 1


def test_backfill_preserves_all_historic_and_current_bytes(tmp_path, analysis):
    repo = Repository(tmp_path / "history.db")
    with repo._connect() as conn:
        old = retain(conn, analysis)
        analysis.review.revision = 1
        analysis.declarations[DC.NET_QUANTITY].raw = "Updated source text"
        current = retain(conn, analysis)
        upgrade_catalog(conn)
        upgrade_catalog(conn)
    assert [r["record"] for r in query(repo, "SELECT record FROM inspection_revision ORDER BY revision")] == [old, current]
    assert query(repo, "SELECT record FROM inspection")[0]["record"] == current
    assert len(query(repo, "SELECT * FROM compliance_finding")) == 2
    assert len(query(repo, "SELECT * FROM extracted_declaration")) == 2


def test_unreadable_history_is_reported_without_losing_good_records(tmp_path, analysis):
    repo = Repository(tmp_path / "damaged.db")
    with repo._connect() as conn:
        retain(conn, analysis)
        conn.execute("INSERT INTO inspection_revision VALUES (?,?,?,?,?,?,?)",
                     (analysis.scan.scan_id, 7, "2026-01-01", None, "legacy", "bad test record", "{broken"))
        upgrade_catalog(conn)
    issue = query(repo, "SELECT * FROM catalog_migration_issue WHERE source_kind='inspection_revision'")[0]
    assert issue["source_key"] == "CAT-1:7" and issue["resolved_at"] is None
    assert len(query(repo, "SELECT * FROM compliance_finding")) == 1
    assert query(repo, "SELECT record FROM inspection_revision WHERE revision=7")[0]["record"] == "{broken"


def test_rule_archive_hash_and_definition_cannot_be_rewritten(tmp_path, pack):
    repo = Repository(tmp_path / "rule-integrity.db")
    repo.archive_rules(pack)
    with repo._connect() as conn:
        raw = json.loads(conn.execute("SELECT record FROM rule_version").fetchone()[0])
        raw["rules"][0]["title"] = "Unarchived replacement"
        with pytest.raises(ValueError, match="original archived"):
            index_rule_pack(conn, raw)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE catalog_rule_definition SET title='changed'")


def test_corrupt_rule_archive_is_an_explicit_migration_gap(tmp_path, pack):
    repo = Repository(tmp_path / "corrupt-rules.db")
    payload = pack.model_dump_json()
    with repo._connect() as conn:
        conn.execute("INSERT INTO rule_version VALUES (?,?,?,?,?,?)",
                     (pack.version, hashlib.sha256(b"wrong").hexdigest(), payload, "2026-01-01", None, "test corruption"))
        upgrade_catalog(conn)
    assert query(repo, "SELECT * FROM catalog_rule_definition") == []
    assert "integrity" in query(repo, "SELECT message FROM catalog_migration_issue")[0]["message"]


def test_foreign_keys_and_history_update_guards_are_enforced(tmp_path, analysis, pack):
    repo = Repository(tmp_path / "constraints.db")
    repo.archive_rules(pack)
    repo.save(analysis)
    with repo._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO catalog_product VALUES ('bad','inspection',NULL,'missing','test')")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE compliance_finding SET machine_verdict='VIOLATION'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE compliance_finding SET definition_version=NULL WHERE rule_link_status='resolved'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM finding_evidence")
