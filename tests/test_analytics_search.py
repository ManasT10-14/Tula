"""Operational analytics and history queries against persisted SQLite records."""
from datetime import UTC, datetime
from html import unescape
from urllib.parse import parse_qs, urlsplit

import pytest

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Severity, Verdict
from tula.domain.models import (
    Analysis,
    Citation,
    Declaration,
    Finding,
    PackageFacts,
    ReviewDecision,
    ReviewState,
    Scan,
)
from tula.storage import workflow
from tula.storage.db import Repository

MRP = "LMPCR.R6.1.E.MRP_FORM"
QUANTITY = "LMPCR.R7.NET_QUANTITY_HEIGHT"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def finding(key, verdict, *, rule=MRP, severity=Severity.MAJOR):
    clause = "Rule 6(1)(e)" if rule == MRP else "Rule 7" if rule == QUANTITY else "Rule 6(1)(d)"
    text = ("The maximum retail price declaration includes all taxes" if rule == MRP
            else "Minimum numeral height depends on panel area" if rule == QUANTITY
            else "Month and year of packing declaration")
    message = ("MRP tax wording check" if rule == MRP else "Net quantity numeral height check"
               if rule == QUANTITY else "Packing date format check")
    return Finding(finding_id=key, rule_id=rule, rules_version="analytics-test-v1",
                   citation=Citation(clause=clause, text=text),
                   verdict=verdict, severity=severity,
                   message=message)


def analysis(scan_id, captured, *, brand, generic, manufacturer=None, category="food", region="Pune",
             operator="Asha Patil", inspector_id="I-1", source="inspection", findings=(), decisions=None,
             approved=False, geo=None):
    declarations = {DC.BRAND: Declaration(klass=DC.BRAND, raw=brand, norm={"name": brand.lower()}),
                    DC.GENERIC_NAME: Declaration(klass=DC.GENERIC_NAME, raw=generic, norm={"name": generic.lower()})}
    if manufacturer:
        declarations[DC.MANUFACTURER] = Declaration(klass=DC.MANUFACTURER, raw=manufacturer,
                                                   norm={"name": manufacturer.splitlines()[0]})
    recorded = {key: ReviewDecision(finding_id=key, verdict=value, reason="Analytics fixture review decision",
                                   actor_id=inspector_id, actor_name=operator, created_at=NOW)
                for key, value in (decisions or {}).items()}
    review = ReviewState(status="approved" if approved else "draft", decisions=recorded,
                         approved_by="Test Supervisor" if approved else None,
                         approved_at=NOW if approved else None)
    return Analysis(scan=Scan(scan_id=scan_id, captured_at=datetime.fromisoformat(captured).replace(tzinfo=UTC),
                              operator=operator, inspector_id=inspector_id, source=source, region=region,
                              geo=geo),
                    package=PackageFacts(gtin="8901234567890" if "SHAKTI" in brand.upper() else None),
                    declarations=declarations, findings=list(findings), review=review,
                    intelligence={"product": {"category": category}}, rules_version="analytics-test-v1")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "datetime", FrozenDatetime)
    repo = Repository(tmp_path / "analytics.sqlite3")
    records = [
        analysis("INS-A-REVIEW", "2026-09-07T10:00:00", brand="Shakti Gold", generic="Biscuits",
                 manufacturer="Acme Foods\nPlot 24 Pune 411018", findings=[
                     finding("A-MRP", Verdict.VIOLATION),
                     finding("A-DATE", Verdict.INCONCLUSIVE, rule="LMPCR.R6.DATE")]),
        analysis("INS-B-NONCOMPLIANT", "2026-09-06T10:00:00", brand="Shakti Select", generic="Coffee",
                 manufacturer="Acme Foods\nPlot 24 Pune 411018", findings=[
                     finding("B-MRP", Verdict.VIOLATION),
                     finding("B-NQ", Verdict.VIOLATION, rule=QUANTITY, severity=Severity.CRITICAL)],
                 decisions={"B-MRP": Verdict.VIOLATION, "B-NQ": Verdict.VIOLATION}, approved=True),
        analysis("INS-C-COMPLIANT", "2026-09-01T10:00:00", brand="Clean Home", generic="Detergent",
                 manufacturer="Clean Home Co", category="household", region="Delhi", operator="Dev Rao", inspector_id="I-2",
                 findings=[finding("C-MRP", Verdict.VIOLATION)], decisions={"C-MRP": Verdict.PASS}, approved=True),
        analysis("INS-D-NOT-APPLICABLE", "2026-08-31T23:59:59", brand="Build Strong", generic="Cement",
                 category="industrial", region="", operator="Dev Rao", inspector_id="I-2",
                 findings=[finding("D-NA", Verdict.NOT_APPLICABLE)], approved=True),
        analysis("BENCH-E", "2026-09-07T11:00:00", brand="Bench Demo", generic="Biscuits", manufacturer="Bench Co",
                 source="bench", findings=[finding("E-MRP", Verdict.VIOLATION),
                                           finding("E-NQ", Verdict.VIOLATION, rule=QUANTITY)]),
        analysis("INS-F-UNREVIEWED", "2026-08-20T10:00:00", brand="Everyday", generic="Tea", manufacturer="Other Foods",
                 region="Mumbai", operator="Lina Das", inspector_id="I-3", findings=[finding("F-PASS", Verdict.PASS)]),
    ]
    for record in records:
        repo.save(record)
    return repo


def ids(result):
    return {row["scan_id"] for row in result["rows"]}


def test_compliance_trend_excludes_pending_and_inapplicable_instead_of_showing_zero(repository):
    series = {row["day"]: row for row in repository.analytics()["compliance_trend"]}
    assert series["2026-09-07"]["percentage"] is None
    assert series["2026-09-07"]["excluded"] == 1
    assert series["2026-08-31"]["percentage"] is None
    assert series["2026-09-01"]["percentage"] == 100.0
    assert series["2026-09-06"]["percentage"] == 0.0
    approved = analysis("INS-SAME-DAY", "2026-09-06T14:00:00", brand="Tea", generic="Tea",
                        findings=[finding("NEW-PASS", Verdict.PASS)], approved=True)
    repository.save(approved)
    day = next(row for row in repository.analytics()["compliance_trend"] if row["day"] == "2026-09-06")
    assert (day["total"], day["assessed"], day["percentage"]) == (2, 2, 50.0)


def test_date_filters_are_utc_and_recent_counts_do_not_include_future_captures(repository):
    crossed = analysis("OFFSET-DATE", "2026-09-01T01:00:00", brand="Offset", generic="Test")
    crossed.scan.captured_at = datetime.fromisoformat("2026-09-01T01:00:00+05:30")
    repository.save(crossed)
    found = repository.filter_search("OFFSET-DATE", date_from="2026-08-31", date_to="2026-08-31")
    assert ids(found) == {"OFFSET-DATE"}
    assert found["rows"][0]["captured_utc"] == "2026-08-31T19:30:00Z"
    assert "OFFSET-DATE" in ids(repository.filter_search("August 2026"))
    before = repository.analytics()
    repository.save(analysis("FUTURE-CAPTURE", "2026-09-30T00:00:00", brand="Future", generic="Test"))
    after = repository.analytics()
    assert after["inspections"] == before["inspections"] + 1
    assert (after["this_week"], after["this_month"]) == (before["this_week"], before["this_month"])


def test_repository_indexes_cover_utc_date_ranges_and_recent_order(repository):
    """High-volume pages must not sort or scan every retained inspection."""
    with repository._connect() as conn:
        recent = [row["detail"] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT scan_id FROM inspection i WHERE i.source=? "
            "ORDER BY julianday(captured_at) DESC,scan_id DESC LIMIT 25",
            ("inspection",),
        )]
        ranged = [row["detail"] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM inspection i WHERE i.source=? "
            "AND date(i.captured_at)>=? AND date(i.captured_at)<=?",
            ("inspection", "2026-08-01", "2026-08-31"),
        )]
        located = [row["detail"] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT latitude,longitude FROM inspection i WHERE i.source=? "
            "AND i.latitude IS NOT NULL AND i.longitude IS NOT NULL "
            "AND ROUND(i.latitude,3)=? AND ROUND(i.longitude,3)=? "
            "ORDER BY julianday(i.captured_at) DESC,i.scan_id DESC",
            ("inspection", 12.972, 77.595),
        )]
    assert any("idx_inspection_source_time" in detail for detail in recent)
    assert all("TEMP B-TREE" not in detail for detail in recent)
    assert any("idx_inspection_source_day" in detail for detail in ranged)
    assert any("idx_inspection_source_geo_recent" in detail for detail in located)
    assert all("TEMP B-TREE" not in detail for detail in located)


def test_coordinate_analytics_are_real_scoped_rounded_and_accessible(repository):
    repository.save(analysis("GEO-BENGALURU-1", "2026-09-05T09:00:00", brand="Geo one", generic="Test",
                             region="Bengaluru Urban", geo=(12.97161, 77.59461),
                             findings=[finding("GEO-FLAG", Verdict.VIOLATION)]))
    repository.save(analysis("GEO-BENGALURU-2", "2026-09-05T10:00:00", brand="Geo two", generic="Test",
                             region="Bengaluru Urban", geo=[12.97162, 77.59462]))
    repository.save(analysis("GEO-OUTSIDE", "2026-09-05T11:00:00", brand="Geo outside", generic="Test",
                             region="London", geo=(51.5074, -0.1278)))
    repository.save(analysis("GEO-BENCH", "2026-09-05T12:00:00", brand="Geo bench", generic="Test",
                             source="bench", geo=(28.6139, 77.209)))
    stats = repository.analytics(source="inspection", date_from="2026-09-05", date_to="2026-09-05")
    assert stats["geo_record_count"] == 3 and stats["mapped_geo_record_count"] == 2
    assert len(stats["geo_points"]) == 2 and len(stats["mapped_geo_points"]) == 1
    point = stats["mapped_geo_points"][0]
    assert (point["latitude"], point["longitude"], point["n"], point["potential"]) == (12.972, 77.595, 2, 1)
    assert 70 <= point["x"] <= 670 and 50 <= point["y"] <= 450 and point["radius"] > 5
    outside = next(row for row in stats["geo_points"] if not row["within_india_extent"])
    assert (outside["label"], outside["n"]) == ("London", 1)
    assert repository.analytics(source="bench")["geo_record_count"] == 1


def test_coordinate_group_filter_matches_dashboard_rounding_and_combines_scope(repository):
    repository.save(analysis("GEO-GROUP-A", "2026-09-05T09:00:00", brand="Geo A", generic="Test",
                             geo=(12.97161, 77.59461)))
    repository.save(analysis("GEO-GROUP-B", "2026-09-05T10:00:00", brand="Geo B", generic="Test",
                             geo=(12.97199, 77.59499)))
    repository.save(analysis("GEO-OTHER-GROUP", "2026-09-05T11:00:00", brand="Geo C", generic="Test",
                             geo=(12.97251, 77.59551)))
    repository.save(analysis("GEO-ZERO", "2026-09-05T12:00:00", brand="Geo zero", generic="Test",
                             geo=(0, 0)))
    filters = {"source": "inspection", "date_from": "2026-09-05", "date_to": "2026-09-05",
               "geo_lat": "12.972", "geo_lon": "77.595"}
    assert ids(repository.filter_search(**filters)) == {"GEO-GROUP-A", "GEO-GROUP-B"}
    assert repository.filter_search(**filters)["total"] == 2
    assert ids(repository.filter_search(source="inspection", geo_lat=0, geo_lon=0)) == {"GEO-ZERO"}


@pytest.mark.parametrize("latitude,longitude", [
    ("", "77.595"), ("12.972", ""), ("91", "77"), ("12", "181"),
    ("nan", "77"), ("not-a-number", "77"),
])
def test_coordinate_group_filter_rejects_incomplete_or_invalid_pairs(repository, latitude, longitude):
    with pytest.raises(ValueError, match="latitude and longitude"):
        repository.filter_search(geo_lat=latitude, geo_lon=longitude)


def test_coordinate_migration_backfills_columns_without_rewriting_records(repository):
    located = analysis("GEO-LEGACY", "2026-09-05T09:00:00", brand="Legacy geo", generic="Test",
                       geo=(19.076, 72.8777))
    repository.save(located)
    with repository._connect() as conn:
        before = conn.execute("SELECT record FROM inspection WHERE scan_id='GEO-LEGACY'").fetchone()[0]
        conn.execute("DROP INDEX idx_inspection_source_geo")
        conn.execute("DROP INDEX idx_inspection_source_geo_recent")
        conn.execute("ALTER TABLE inspection DROP COLUMN latitude")
        conn.execute("ALTER TABLE inspection DROP COLUMN longitude")
    restored = Repository(repository.path)
    with restored._connect() as conn:
        row = conn.execute("SELECT record,latitude,longitude FROM inspection WHERE scan_id='GEO-LEGACY'").fetchone()
    assert row["record"] == before
    assert (row["latitude"], row["longitude"]) == pytest.approx((19.076, 72.8777))


def test_coordinate_migration_keeps_minimal_legacy_row_available(repository):
    with repository._connect() as conn:
        conn.execute("INSERT INTO inspection (scan_id,captured_at,record) VALUES (?,?,?)",
                     ("MINIMAL-LEGACY", "2026-09-05T09:00:00+00:00", "{}"))
        conn.execute("INSERT INTO inspection_revision VALUES (?,?,?,?,?,?,?)",
                     ("MINIMAL-LEGACY", 0, "2026-09-05T09:00:00+00:00", None,
                      "legacy.import", "Minimal retained compatibility row", "{}"))
        before = conn.execute("SELECT record FROM inspection WHERE scan_id='MINIMAL-LEGACY'").fetchone()[0]
        conn.execute("DROP INDEX idx_inspection_source_geo")
        conn.execute("DROP INDEX idx_inspection_source_geo_recent")
        conn.execute("ALTER TABLE inspection DROP COLUMN latitude")
        conn.execute("ALTER TABLE inspection DROP COLUMN longitude")
    restored = Repository(repository.path)
    with restored._connect() as conn:
        row = conn.execute("SELECT record,latitude,longitude FROM inspection WHERE scan_id='MINIMAL-LEGACY'").fetchone()
    assert row["record"] == before == "{}"
    assert row["latitude"] is None and row["longitude"] is None


@pytest.mark.parametrize("geo", [[91, 10], [10, 181], [float("inf"), 10], [True, 10], [10], "12,77"])
def test_scan_rejects_invalid_coordinates(geo):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Location|Latitude|finite|numbers"):
        analysis("BAD-GEO", "2026-09-05T09:00:00", brand="Bad geo", generic="Test", geo=geo)


def test_confirmed_category_changes_search_and_analytics_without_rewriting_original(repository):
    original = repository.revision("INS-A-REVIEW", 0).model_dump_json()
    def attest(a):
        a.package.legal_context = {"category": "cosmetic", "category_confirmed": True}
    repository.revise("INS-A-REVIEW", 0, "I-1", "package.context_confirmed", "Verified package category", attest)
    row = repository.filter_search("INS-A-REVIEW")["rows"][0]
    assert (row["category"], row["category_basis"]) == ("cosmetic", "officer_confirmed")
    assert "INS-A-REVIEW" not in ids(repository.filter_search(category="food"))
    assert repository.revision("INS-A-REVIEW", 0).model_dump_json() == original
    assert repository.get("INS-A-REVIEW").intelligence["product"]["category"] == "food"
    counts = {x["label"]: x["n"] for x in repository.analytics()["category_basis"]}
    assert counts["officer_confirmed"] == 1
    repository.revise("INS-A-REVIEW", 1, "I-1", "package.context_confirmed", "Category no longer established",
                       lambda a: a.package.legal_context.update(category="unknown", category_confirmed=False))
    row = repository.filter_search("INS-A-REVIEW")["rows"][0]
    assert (row["category"], row["category_basis"]) == ("food", "ocr_suggested")


def test_category_index_upgrade_preserves_retained_record_bytes(repository):
    with repository._connect() as conn:
        before = conn.execute("SELECT record FROM inspection WHERE scan_id='INS-A-REVIEW'").fetchone()[0]
        conn.execute("ALTER TABLE inspection DROP COLUMN category_basis")
    restored = Repository(repository.path)
    with restored._connect() as conn:
        after = conn.execute("SELECT record FROM inspection WHERE scan_id='INS-A-REVIEW'").fetchone()[0]
    assert after == before
    assert restored.filter_search("INS-A-REVIEW")["rows"][0]["category_basis"] == "ocr_suggested"


@pytest.mark.parametrize("method,kwargs", [("analytics", {"source": "all"}),
    ("analytics", {"date_from": "2026-09-02", "date_to": "2026-09-01"}),
    ("filter_search", {"date_from": "2026-09-02", "date_to": "2026-09-01"})])
def test_invalid_dashboard_ranges_have_actionable_errors(repository, method, kwargs):
    with pytest.raises(ValueError, match="Choose operational|From date"):
        getattr(repository, method)(**kwargs)


def test_product_history_keeps_bench_separate_and_exposes_reviewed_result(repository):
    demo = analysis("BENCH-SHAKTI", "2026-09-07T12:00:00", brand="Shakti", generic="Biscuits", source="bench")
    repository.save(demo)
    operational = repository.history("8901234567890", source="inspection")
    assert {row["scan_id"] for row in operational} == {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}
    assert operational[0]["product_status"] == "NON_COMPLIANT"
    assert operational[1]["product_status"] == "NEEDS_REVIEW"
    assert repository.history("8901234567890", source="bench")[0]["scan_id"] == "BENCH-SHAKTI"


def test_external_save_transaction_rolls_back_all_revision_and_catalog_rows(repository):
    new = analysis("ROLLBACK-CHECK", "2026-09-07T12:00:00", brand="Rollback", generic="Test")
    with pytest.raises(RuntimeError, match="abort caller"), repository._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        repository.save(new, connection=conn)
        assert conn.execute("SELECT 1 FROM inspection_product_revision WHERE scan_id=?", (new.scan.scan_id,)).fetchone()
        raise RuntimeError("abort caller")
    assert repository.get(new.scan.scan_id) is None
    assert not repository.revisions(new.scan.scan_id)


@pytest.mark.parametrize("query,expected", [
    ("SHAKTI", {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}),
    ("shak bisc", {"INS-A-REVIEW"}),
    ("shakti tax", {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}),
    ("Rule 7", {"INS-B-NONCOMPLIANT", "BENCH-E"}),
    ("num height", {"INS-B-NONCOMPLIANT", "BENCH-E"}),
    ("INS-B-NON", {"INS-B-NONCOMPLIANT"}),
    ("8901234567890", {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}),
    ("nobody-here", set()),
    ("' OR 1=1 --", set()),
])
def test_partial_multiterm_search_is_bound_to_persisted_records(repository, query, expected):
    result = repository.filter_search(query)
    assert ids(result) == expected
    assert result["total"] == len(expected)
    assert all("record" not in row for row in result["rows"])


@pytest.mark.parametrize("query,expected", [
    ("August 2026", {"INS-D-NOT-APPLICABLE", "INS-F-UNREVIEWED"}),
    ("Sep 2026", {"INS-A-REVIEW", "INS-B-NONCOMPLIANT", "INS-C-COMPLIANT", "BENCH-E"}),
    ("July 2026", set()),
])
def test_explicit_month_phrase_search(repository, query, expected):
    assert ids(repository.filter_search(query)) == expected


@pytest.mark.parametrize("filters,expected", [
    ({"category": "household"}, {"INS-C-COMPLIANT"}),
    ({"review_status": "approved"}, {"INS-B-NONCOMPLIANT", "INS-C-COMPLIANT", "INS-D-NOT-APPLICABLE"}),
    ({"product_status": "NON_COMPLIANT"}, {"INS-B-NONCOMPLIANT"}),
    ({"product_status": "COMPLIANT"}, {"INS-C-COMPLIANT"}),
    ({"source": "bench"}, {"BENCH-E"}),
    ({"region": "Pune", "source": "inspection"}, {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}),
    ({"inspector_id": "I-3"}, {"INS-F-UNREVIEWED"}),
    ({"operator": "DEV"}, {"INS-C-COMPLIANT", "INS-D-NOT-APPLICABLE"}),
    ({"manufacturer": "acME"}, {"INS-A-REVIEW", "INS-B-NONCOMPLIANT"}),
    ({"rule_id": QUANTITY, "source": "inspection"}, {"INS-B-NONCOMPLIANT"}),
    ({"overall": "VIOLATION", "source": "inspection"}, {"INS-A-REVIEW", "INS-B-NONCOMPLIANT", "INS-C-COMPLIANT"}),
    ({"date_from": "2026-09-01", "date_to": "2026-09-06", "source": "inspection"}, {"INS-B-NONCOMPLIANT", "INS-C-COMPLIANT"}),
    ({"category": "food", "region": "Pune", "review_status": "approved", "operator": "asha", "source": "inspection"}, {"INS-B-NONCOMPLIANT"}),
])
def test_all_history_filters_combine_with_and(repository, filters, expected):
    assert ids(repository.filter_search(**filters)) == expected


def test_pagination_is_stable_and_does_not_duplicate_or_drop_rows(repository):
    pages = [repository.filter_search(page=page, page_size=2) for page in (1, 2, 3)]
    sequence = [row["scan_id"] for page in pages for row in page["rows"]]
    assert sequence == ["BENCH-E", "INS-A-REVIEW", "INS-B-NONCOMPLIANT", "INS-C-COMPLIANT", "INS-D-NOT-APPLICABLE", "INS-F-UNREVIEWED"]
    assert all(page["total"] == 6 and page["pages"] == 3 for page in pages)
    assert len(set(sequence)) == 6
    assert repository.filter_search(page=4, page_size=2)["rows"] == []
    assert repository.filter_search(page=-1, page_size=10000)["page_size"] == 100
    assert repository.filter_search(page=-1)["page"] == 1


def test_date_bounds_are_inclusive_and_invalid_dates_fail(repository):
    assert ids(repository.filter_search(date_from="2026-08-31", date_to="2026-08-31")) == {"INS-D-NOT-APPLICABLE"}
    with pytest.raises(ValueError):
        repository.filter_search(date_from="2026-02-30")
    with pytest.raises(ValueError):
        repository.analytics(date_to="not-a-date")


def test_operational_counts_exclude_bench_and_separate_review_from_machine(repository):
    stats = repository.analytics()
    assert {key: stats[key] for key in ["inspections", "potential_violations", "verified_violations", "compliant", "non_compliant", "needs_review", "approved", "this_week", "this_month"]} == {
        "inspections": 5, "potential_violations": 4, "verified_violations": 2,
        "compliant": 1, "non_compliant": 1, "needs_review": 2, "approved": 3, "this_week": 3, "this_month": 3}
    assert stats["compliance_percentage"] == 50.0
    bench = repository.analytics(source="bench")
    assert bench["inspections"] == 1 and bench["potential_violations"] == 2
    assert bench["verified_violations"] == 0 and bench["non_compliant"] == 0
    assert bench["compliance_percentage"] is None


def test_category_manufacturer_inspector_region_and_rule_counts_are_actual_records(repository):
    stats = repository.analytics()
    expected = {
        "categories": {"food": 3, "household": 1, "industrial": 1},
        "manufacturers": {"Acme Foods": 2, "Clean Home Co": 1, "Other Foods": 1, "Unspecified": 1},
        "activity": {"Asha Patil": 2, "Dev Rao": 2, "Lina Das": 1},
        "regions": {"Pune": 2, "Delhi": 1, "Mumbai": 1, "Unspecified": 1},
        "by_rule": {MRP: 3, QUANTITY: 1},
        "severity": {"major": 3, "critical": 1},
    }
    for key, values in expected.items():
        assert {item["label"]: item["n"] for item in stats[key]} == values
    assert {item["label"]: item["potential"] for item in stats["manufacturers"]}["Acme Foods"] == 3


def test_time_series_and_date_filters_share_operational_scope(repository):
    stats = repository.analytics(date_from="2026-09-01", date_to="2026-09-06")
    assert stats["inspections"] == 2 and stats["verified_violations"] == 2
    assert stats["trend"] == [
        {"day": "2026-09-01", "total": 1, "compliant": 1, "non_compliant": 0},
        {"day": "2026-09-06", "total": 1, "compliant": 0, "non_compliant": 1}]
    empty = repository.analytics(date_from="2020-01-01", date_to="2020-12-31")
    assert empty["inspections"] == 0 and empty["compliance_percentage"] is None
    assert not empty["categories"] and not empty["by_rule"]


def test_revision_history_does_not_inflate_current_analytics_or_search(repository):
    before = repository.analytics()
    for revision in range(3):
        repository.revise("INS-B-NONCOMPLIANT", revision, "I-1", "inspection.comment", "Analytics fixture comment",
                          lambda a: a.review.comments.append({"text": "Historical test comment"}))
    after = repository.analytics()
    for key in ["inspections", "potential_violations", "verified_violations", "approved", "compliant", "non_compliant"]:
        assert before[key] == after[key]
    assert repository.filter_search("INS-B-NONCOMPLIANT")["total"] == 1
    assert len(repository.revisions("INS-B-NONCOMPLIANT")) == 4
    assert repository.revision("INS-B-NONCOMPLIANT", 0).review.comments == []
    assert len(repository.revision("INS-B-NONCOMPLIANT", 3).review.comments) == 3


def test_comment_only_revision_preserves_grouping_and_pending_review_count(repository):
    before = repository.filter_search("INS-A-REVIEW")["rows"][0]
    groups = repository.analytics()["manufacturers"]
    repository.revise("INS-A-REVIEW", 0, "I-1", "inspection.comment", "No product change",
                      lambda a: a.review.comments.append({"text": "Comment only"}))
    after = repository.filter_search("INS-A-REVIEW")["rows"][0]
    assert after["brand"] == before["brand"]
    assert after["generic_name"] == before["generic_name"]
    assert after["manufacturer"] == before["manufacturer"]
    assert after["n_inconclusive"] == before["n_inconclusive"] == len(repository.get("INS-A-REVIEW").pending_review)
    assert repository.analytics()["manufacturers"] == groups


def test_search_can_find_legal_rule_text_beyond_short_machine_message(repository):
    assert ids(repository.filter_search("maximum retail")) == {
        "INS-A-REVIEW", "INS-B-NONCOMPLIANT", "INS-C-COMPLIANT", "INS-D-NOT-APPLICABLE", "BENCH-E", "INS-F-UNREVIEWED"}


def test_literal_percent_search_is_not_a_match_everything_wildcard(repository):
    assert repository.filter_search("100%")["total"] == 0
    assert repository.filter_search("%")["total"] == 0
    # Every fixture has a literal underscore in its rule ID, so '_' is a valid
    # match. 'ins_a' must not use underscore to match the hyphen in INS-A.
    assert repository.filter_search("ins_a")["total"] == 0
    assert repository.filter_search(manufacturer="%")["total"] == 0


def test_literal_search_retains_real_punctuation_and_rule_number_boundaries(repository):
    literal = analysis("LITERAL", "2026-09-07T12:00:00", brand="Salt_Unit 100%", generic="Salt",
                       manufacturer="Factory_One 100%")
    lookalike = analysis("LOOKALIKE", "2026-09-07T12:01:00", brand="SaltXUnit 1000", generic="Salt",
                         manufacturer="FactoryXOne 1000", findings=[
                             finding("R70", Verdict.PASS).model_copy(update={
                                 "citation": Citation(clause="Rule 70(1)", text="Different rule")})])
    repository.save(literal)
    repository.save(lookalike)
    assert ids(repository.filter_search("Salt_Unit")) == {"LITERAL"}
    assert ids(repository.filter_search("100%")) == {"LITERAL"}
    assert ids(repository.filter_search(manufacturer="Factory_One")) == {"LITERAL"}
    assert ids(repository.filter_search("Rule 7")) == {"INS-B-NONCOMPLIANT", "BENCH-E"}
    assert ids(repository.filter_search("Rule 70")) == {"LOOKALIKE"}


def test_citation_search_migration_preserves_old_records(tmp_path):
    import json
    import sqlite3

    from tula.storage.db import SCHEMA

    path = tmp_path / "legacy.sqlite3"
    record = analysis("LEGACY", "2026-09-07T12:00:00", brand="Historic", generic="Tea",
                       findings=[finding("OLD-MRP", Verdict.VIOLATION)])
    raw = json.dumps(record.model_dump(mode="json"), indent=3)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO inspection(scan_id,captured_at,record,overall) VALUES (?,?,?,?)",
                     (record.scan.scan_id, record.scan.captured_at.isoformat(), raw, "VIOLATION"))
        conn.execute("INSERT INTO finding(scan_id,rule_id,clause,verdict,message) VALUES (?,?,?,?,?)",
                     ("LEGACY", MRP, "Rule 6(1)(e)", "VIOLATION", "MRP tax wording check"))
    migrated = Repository(path)
    assert ids(migrated.filter_search("maximum retail")) == {"LEGACY"}
    assert ids(migrated.filter_search(rule_id=MRP, finding_outcome="FLAGGED")) == {"LEGACY"}
    assert migrated.filter_search(rule_id=MRP, finding_outcome="PASS")["total"] == 0
    assert "unknown" in {choice["value"] for choice in migrated.filter_search()["category_choices"]}
    with migrated._connect() as conn:
        assert conn.execute("SELECT record FROM inspection").fetchone()[0] == raw
    assert ids(Repository(path).filter_search("maximum retail")) == {"LEGACY"}


def test_updated_findings_replace_current_aggregates_but_preserve_history(repository):
    before = repository.analytics()

    def resolve_machine_flag(a):
        a.findings = [f.model_copy(update={"verdict": Verdict.PASS}) if f.finding_id == "A-MRP" else f
                      for f in a.findings]
    repository.revise("INS-A-REVIEW", 0, "I-1", "declaration.corrected", "Test corrected MRP text", resolve_machine_flag)
    after = repository.analytics()
    assert after["inspections"] == before["inspections"]
    assert after["potential_violations"] == before["potential_violations"] - 1
    assert {row["label"]: row["n"] for row in after["by_rule"]}[MRP] == 2
    assert "INS-A-REVIEW" not in ids(repository.filter_search("violation", source="inspection"))
    assert repository.revision("INS-A-REVIEW", 0).findings[0].verdict is Verdict.VIOLATION
    assert repository.get("INS-A-REVIEW").findings[0].verdict is Verdict.PASS


def test_category_and_region_changes_update_current_groupings_only(repository):
    def amend_context(a):
        a.intelligence["product"]["category"] = "beverage"
        a.scan.region = "Nashik"
    repository.revise("INS-A-REVIEW", 0, "I-1", "inspection.context", "Test reviewed product context", amend_context)
    stats = repository.analytics()
    categories = {row["label"]: row["n"] for row in stats["categories"]}
    regions = {row["label"]: row["n"] for row in stats["regions"]}
    assert categories["beverage"] == 1 and categories["food"] == 2
    assert regions["Nashik"] == 1 and regions["Pune"] == 1
    assert ids(repository.filter_search(category="beverage", region="Nashik")) == {"INS-A-REVIEW"}
    assert repository.revision("INS-A-REVIEW", 0).scan.region == "Pune"


def test_idempotent_save_does_not_create_extra_analytics_rows(repository):
    original = repository.get("INS-A-REVIEW")
    repository.save(original)
    assert repository.analytics()["inspections"] == 5
    assert len(repository.revisions("INS-A-REVIEW")) == 1
    changed = original.model_copy(deep=True)
    changed.scan.region = "Changed"
    with pytest.raises(ValueError, match="audited revision"):
        repository.save(changed)
    assert repository.get("INS-A-REVIEW").scan.region == "Pune"


def test_reopened_repository_preserves_index_and_revision_counts(repository):
    again = Repository(repository.path)
    assert again.analytics() == repository.analytics()
    assert again.filter_search("Shakti")["total"] == 2


@pytest.fixture
def finding_records(repository):
    """Opposite outcomes on different rules expose incorrect separate EXISTS filters."""
    for key, target, other, source, captured in [
        ("TARGET-PASS", Verdict.PASS, Verdict.VIOLATION, "inspection", "2026-09-04T10:00:00"),
        ("TARGET-VIOLATION", Verdict.VIOLATION, Verdict.PASS, "inspection", "2026-09-04T11:00:00"),
        ("TARGET-ADVISORY", Verdict.ADVISORY, Verdict.PASS, "inspection", "2026-09-04T12:00:00"),
        ("TARGET-UNCERTAIN", Verdict.INCONCLUSIVE, Verdict.VIOLATION, "inspection", "2026-09-04T13:00:00"),
        ("TARGET-NONE", None, Verdict.VIOLATION, "inspection", "2026-09-04T14:00:00"),
        ("TARGET-BENCH", Verdict.VIOLATION, Verdict.PASS, "bench", "2026-09-04T15:00:00"),
        ("TARGET-OLD", Verdict.VIOLATION, Verdict.PASS, "inspection", "2026-08-04T10:00:00"),
    ]:
        findings = [finding(key + "-OTHER", other, rule="FILTER.OTHER")]
        if target is not None:
            findings.append(finding(key + "-TARGET", target, rule="FILTER.TARGET"))
        repository.save(analysis(key, captured, brand="Navigation", generic="Test package",
                                 source=source, findings=findings))
    return repository


@pytest.mark.parametrize("outcome,expected", [
    ("FLAGGED", {"TARGET-VIOLATION", "TARGET-ADVISORY"}),
    ("VIOLATION", {"TARGET-VIOLATION"}), ("ADVISORY", {"TARGET-ADVISORY"}),
    ("PASS", {"TARGET-PASS"}), ("INCONCLUSIVE", {"TARGET-UNCERTAIN"}),
    ("UNVERIFIED", set()), ("EXEMPT", set()), ("NOT_APPLICABLE", set()),
])
def test_finding_outcome_is_bound_to_the_same_rule_and_scope(finding_records, outcome, expected):
    result = finding_records.filter_search("Navigation", rule_id="FILTER.TARGET", finding_outcome=outcome,
                                           source="inspection", date_from="2026-09-01", date_to="2026-09-30")
    assert ids(result) == expected


def test_finding_filter_preserves_aggregate_machine_semantics_and_optional_rule(finding_records):
    scope = {"source": "inspection", "date_from": "2026-09-01", "date_to": "2026-09-30"}
    # The selected rule passed even though another rule caused an overall violation.
    assert ids(finding_records.filter_search("Navigation", rule_id="FILTER.TARGET", finding_outcome="PASS",
                                             overall="VIOLATION", **scope)) == {"TARGET-PASS"}
    assert ids(finding_records.filter_search("Navigation", finding_outcome="ADVISORY", **scope)) == {"TARGET-ADVISORY"}
    assert ids(finding_records.filter_search("Navigation", rule_id="FILTER.TARGET", **scope)) == {
        "TARGET-PASS", "TARGET-VIOLATION", "TARGET-ADVISORY", "TARGET-UNCERTAIN"}
    assert finding_records.filter_search(rule_id="FILTER.TARGET.other", finding_outcome="FLAGGED")["total"] == 0
    assert ids(finding_records.filter_search(rule_id="FILTER.TARGET", finding_outcome="FLAGGED",
                                             source="bench")) == {"TARGET-BENCH"}
    assert ids(finding_records.filter_search("August 2026", rule_id="FILTER.TARGET", finding_outcome="FLAGGED",
                                             source="inspection")) == {"TARGET-OLD"}


def test_finding_filter_uses_current_findings_without_duplicate_rows(finding_records):
    before = finding_records.revision("TARGET-VIOLATION", 0).model_dump_json()

    def replace_findings(record):
        record.findings = [f.model_copy(update={"verdict": Verdict.PASS}) for f in record.findings]

    finding_records.revise("TARGET-VIOLATION", 0, "I-1", "declaration.corrected", "Reviewed target field", replace_findings)
    assert "TARGET-VIOLATION" not in ids(finding_records.filter_search(rule_id="FILTER.TARGET", finding_outcome="FLAGGED"))
    assert finding_records.revision("TARGET-VIOLATION", 0).model_dump_json() == before
    duplicate = analysis("MULTIPLE-FINDINGS", "2026-09-07T10:00:00", brand="Multiple", generic="Test", findings=[
        finding("DUP-1", Verdict.VIOLATION, rule="FILTER.TARGET"),
        finding("DUP-2", Verdict.ADVISORY, rule="FILTER.TARGET")])
    finding_records.save(duplicate)
    assert finding_records.filter_search("Multiple", rule_id="FILTER.TARGET", finding_outcome="FLAGGED")["total"] == 1


@pytest.mark.parametrize("invalid", ["violation", "VIOLATION,ADVISORY", "anything", "PASS' OR 1=1 --"])
def test_invalid_finding_outcomes_are_rejected(repository, invalid):
    with pytest.raises(ValueError, match="finding outcome"):
        repository.filter_search(finding_outcome=invalid)


def test_category_choices_cover_confirmed_legacy_unknown_and_retained_labels(repository):
    from tula.services.context import CATEGORIES

    for category in sorted(CATEGORIES - {"unknown"}):
        record = analysis("CONFIRMED-" + category, "2026-09-07T10:00:00", brand="Context", generic="Test")
        record.package.legal_context = {"category": category, "category_confirmed": True}
        repository.save(record)
        assert record.scan.scan_id in ids(repository.filter_search(category=category))
    for category in ("personal_care", "household", "industrial", "unknown", "legacy_beverage"):
        record = analysis("CATEGORY-" + category, "2026-09-07T10:00:00", brand="Retained", generic="Test", category=category)
        repository.save(record)
    original = repository.get("CATEGORY-legacy_beverage").model_dump_json()
    choices = {item["value"] for item in repository.filter_search()["category_choices"]}
    assert choices >= CATEGORIES | {"personal_care", "household", "industrial", "legacy_beverage"}
    assert ids(repository.filter_search(category="unknown")) == {"CATEGORY-unknown"}
    assert ids(repository.filter_search(category="legacy_beverage")) == {"CATEGORY-legacy_beverage"}
    assert repository.get("CATEGORY-legacy_beverage").model_dump_json() == original
    # Keep a requested unknown value visible even when it has no matching record.
    empty = repository.filter_search(category="unlisted_category")
    assert empty["total"] == 0 and "unlisted_category" in {c["value"] for c in empty["category_choices"]}


@pytest.fixture
def navigation_client(repository, monkeypatch, tmp_path):
    """Real auth, route functions and templates against the isolated saved records."""
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tula.security import install_security
    from tula.security.web import LOGIN_CSRF_COOKIE

    monkeypatch.setenv("TULA_DATA_DIR", str(tmp_path / "web-bootstrap"))
    web = importlib.import_module("tula.web.app")
    monkeypatch.setattr(web, "repo", repository)
    app = FastAPI()
    security = install_security(app, repository.path)
    secret = "Navigation test passphrase 2026!"
    admin = security.create_user("navigation-admin", secret, role="admin", bootstrap=True)
    security.create_user("navigation-inspector", secret, role="inspector", actor_id=admin.id)
    app.add_api_route("/repository", web.repository, methods=["GET"])
    app.add_api_route("/dashboard", web.dashboard, methods=["GET"])
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/login")
        login = client.post("/login", data={"username": "navigation-inspector", "password": secret,
                                            "csrf_token": client.cookies[LOGIN_CSRF_COOKIE]}, follow_redirects=False)
        assert login.status_code == 303
        yield client


def test_dashboard_rule_link_opens_only_matching_flags_and_retains_filters(finding_records, navigation_client):
    import re

    page = navigation_client.get("/dashboard", params={"source": "inspection", "date_from": "2026-09-01", "date_to": "2026-09-30"})
    assert page.status_code == 200
    href = next(unescape(link) for link in re.findall(r'href="([^"]+)"', page.text)
                if "rule_id=FILTER.TARGET" in link)
    query = parse_qs(urlsplit(href).query)
    assert query == {"rule_id": ["FILTER.TARGET"], "finding_outcome": ["FLAGGED"], "source": ["inspection"],
                     "date_from": ["2026-09-01"], "date_to": ["2026-09-30"]}
    stats = finding_records.analytics(source="inspection", date_from="2026-09-01", date_to="2026-09-30")
    assert next(r["n"] for r in stats["by_rule"] if r["label"] == "FILTER.TARGET") == 2
    results = navigation_client.get(href)
    assert results.status_code == 200
    assert set(re.findall(r'href="/inspections/([^"]+)"', results.text)) == {"TARGET-VIOLATION", "TARGET-ADVISORY"}
    assert '<option value="FLAGGED" selected>' in results.text
    assert "Finding outcome applies to the selected Rule ID" in results.text
    assert navigation_client.get("/repository?finding_outcome=INVALID").status_code == 422


def test_dashboard_renders_offline_coordinate_plot_and_accessible_table(repository, navigation_client):
    import re

    repository.save(analysis("GEO-DASHBOARD", "2026-09-05T09:00:00", brand="Geo dashboard", generic="Test",
                             region="Bengaluru Urban", geo=(12.97161, 77.59461),
                             findings=[finding("GEO-DASH-FLAG", Verdict.VIOLATION)]))
    page = navigation_client.get("/dashboard", params={"date_from": "2026-09-05", "date_to": "2026-09-05"})
    assert page.status_code == 200
    assert 'aria-label="India coordinate plot"' in page.text
    assert "Bengaluru Urban · 12.972, 77.595 · 1 inspection(s)" in page.text
    assert 'aria-label="Recorded inspection coordinate data"' in page.text
    assert "rounded to 0.001°" in page.text and "not a boundary or market-prevalence map" in page.text
    links = [unescape(link) for link in re.findall(r'href="([^"]+)"', page.text) if "geo_lat=" in link]
    assert len(links) == 2  # one SVG link and one accessible table link
    query = parse_qs(urlsplit(links[0]).query)
    assert query == {"source": ["inspection"], "date_from": ["2026-09-05"], "date_to": ["2026-09-05"],
                     "geo_lat": ["12.972"], "geo_lon": ["77.595"]}
    results = navigation_client.get(links[0])
    assert results.status_code == 200
    assert set(re.findall(r'href="/inspections/([^"]+)"', results.text)) == {"GEO-DASHBOARD"}
    assert 'name="geo_lat" value="12.972"' in results.text
    assert 'name="geo_lon" value="77.595"' in results.text


def test_repository_route_rejects_invalid_coordinate_filters(navigation_client):
    for query in ("geo_lat=12", "geo_lon=77", "geo_lat=91&geo_lon=77", "geo_lat=nan&geo_lon=77"):
        response = navigation_client.get("/repository?" + query)
        assert response.status_code == 422
        assert "latitude and longitude" in response.text


def test_repository_renders_all_category_choices_and_escapes_retained_labels(repository, navigation_client):
    from tula.services.context import CATEGORIES

    retained = analysis("LEGACY-HTML", "2026-09-07T10:00:00", brand="Legacy", generic="Test", category='<script>alert("x")</script>')
    repository.save(retained)
    for selected in ("cosmetic", "unknown", "personal_care", '<script>alert("x")</script>'):
        response = navigation_client.get("/repository", params={"category": selected})
        assert response.status_code == 200
        assert all(f'<option value="{value}"' in response.text for value in CATEGORIES)
        assert '<script>alert("x")</script>' not in response.text
        assert "&lt;script&gt;" in response.text
    assert 'value="cosmetic" selected' in navigation_client.get("/repository?category=cosmetic").text
    assert 'value="unknown" selected' in navigation_client.get("/repository?category=unknown").text


def test_finding_filter_pagination_retains_query_scope_and_aggregate_outcome(repository, navigation_client):
    import re

    expected = set()
    for index in range(27):
        scan_id = f"PAGE-FLAG-{index:02d}"
        expected.add(scan_id)
        repository.save(analysis(scan_id, "2026-09-05T10:00:00", brand="Pagination", generic="Test", findings=[
            finding(scan_id, Verdict.VIOLATION, rule="PAGINATION.TARGET")]))
    parameters = {"q": "Pagination", "source": "inspection", "date_from": "2026-09-01", "date_to": "2026-09-07",
                  "rule_id": "PAGINATION.TARGET", "finding_outcome": "FLAGGED", "verdict": "VIOLATION", "category": "food"}
    first = navigation_client.get("/repository", params=parameters)
    assert first.status_code == 200
    next_link = unescape(re.search(r'<a href="([^"]+)">Next page', first.text).group(1))
    assert parse_qs(urlsplit(next_link).query) == {**{key: [value] for key, value in parameters.items()}, "page": ["2"]}
    second = navigation_client.get(next_link)
    found_first = set(re.findall(r'href="/inspections/([^"]+)"', first.text))
    found_second = set(re.findall(r'href="/inspections/([^"]+)"', second.text))
    assert len(found_first) == 25 and len(found_second) == 2
    assert found_first.isdisjoint(found_second) and found_first | found_second == expected


def test_dashboard_district_chart_links_scope_and_marks_flagged_locations(repository, navigation_client):
    """Bar colour must follow potential findings, and only real districts link."""
    import re

    repository.save(analysis("DIST-FLAGGED", "2026-09-05T09:00:00", brand="Flagged", generic="Test",
                             region="Nagpur", findings=[finding("DIST-F", Verdict.VIOLATION)]))
    repository.save(analysis("DIST-CLEAN", "2026-09-05T10:00:00", brand="Clean", generic="Test",
                             region="Coimbatore", findings=[finding("DIST-C", Verdict.PASS)]))
    repository.save(analysis("DIST-BLANK", "2026-09-05T11:00:00", brand="Nowhere", generic="Test",
                             findings=[finding("DIST-B", Verdict.PASS)]))

    page = navigation_client.get("/dashboard", params={"date_from": "2026-09-05", "date_to": "2026-09-05"})
    assert page.status_code == 200
    assert "Inspection coverage by district" in page.text
    assert "not market prevalence" in page.text
    # A district carrying a potential finding is amber; a clean one is not.
    flagged = re.search(r'Nagpur.*?<i class="(\w+)"', page.text, re.DOTALL)
    clean = re.search(r'Coimbatore.*?<i class="(\w+)"', page.text, re.DOTALL)
    assert flagged and flagged.group(1) == "flagged"
    assert clean and clean.group(1) == "clear"

    links = {unescape(link) for link in re.findall(r'href="([^"]+)"', page.text) if "region=" in link}
    assert any("region=Nagpur" in link for link in links)
    # 'Unspecified' is a grouping label, not a stored value: linking it would filter to nothing.
    assert not any("Unspecified" in link for link in links)

    target = next(link for link in links if "region=Nagpur" in link)
    assert parse_qs(urlsplit(target).query) == {
        "region": ["Nagpur"], "source": ["inspection"],
        "date_from": ["2026-09-05"], "date_to": ["2026-09-05"]}
    results = navigation_client.get(target)
    assert results.status_code == 200
    assert set(re.findall(r'href="/inspections/([^"]+)"', results.text)) == {"DIST-FLAGGED"}


def test_dashboard_district_names_are_escaped(repository, navigation_client):
    """District text is operator-typed and reaches both a label and an href."""
    repository.save(analysis("DIST-XSS", "2026-09-05T12:00:00", brand="Inject", generic="Test",
                             region='<script>alert("x")</script>', findings=[finding("DIST-X", Verdict.PASS)]))
    page = navigation_client.get("/dashboard", params={"date_from": "2026-09-05", "date_to": "2026-09-05"})
    assert page.status_code == 200
    assert '<script>alert("x")</script>' not in page.text
    assert "&lt;script&gt;" in page.text
