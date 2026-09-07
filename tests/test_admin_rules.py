"""Runtime rule management using real SQLite, sessions, CSRF and FastAPI routes.

Only the expensive OCR call is replaced in the queued-version contract test.
These tests make no claims about legal approval or OCR accuracy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from tula.domain.models import Analysis, PackageFacts, Scan
from tula.rules.engine import RulesEngine
from tula.security import install_security
from tula.security.web import LOGIN_CSRF_COOKIE
from tula.services import jobs as jobs_module
from tula.services.jobs import InspectionJobs
from tula.storage.db import Repository
from tula.web.admin_rules import (
    ACTIVE_KEY,
    MAX_PACK_BYTES,
    install_admin_rules,
    load_active_rules,
    parse_rule_pack,
    validate_expression,
)

PASSWORD = "Rule administration test passphrase 2026!"
TEMPLATES = Path(__file__).parents[1] / "src/tula/web/templates"


def draft(version="admin-test-2"):
    return {
        "version": version, "title": "Test-only rule version",
        "source": "https://consumeraffairs.gov.in/", "notes": "Test fixture; not legal approval.",
        "rules": [{"id": "TEST-1", "title": "Test declaration presence",
                   "citation": {"clause": "Test clause"}, "effective_from": "2020-01-01",
                   "assert": {"present": "$decl.net_quantity"}}],
    }


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "data/tula.db")
    app = FastAPI()
    security = install_security(app, repo.path)
    admin = security.create_user("admin", PASSWORD, role="admin", bootstrap=True)
    security.create_user("inspector", PASSWORD, actor_id=admin.id)
    security.create_user("supervisor", PASSWORD, role="supervisor", actor_id=admin.id)
    rules = RulesEngine(parse_rule_pack(json.dumps(draft("admin-test-1"))))
    repo.archive_rules(rules.pack)
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals["asset_url"] = lambda name: "/static/" + name
    web = SimpleNamespace(app=app, repo=repo, rules=rules, templates=templates)
    queue = InspectionJobs(repo, rules, security)
    monkeypatch.setattr(queue, "start", lambda: None)
    app.state.jobs = queue
    install_admin_rules(web)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield SimpleNamespace(web=web, repo=repo, security=security, admin=admin, client=client,
                              queue=queue, default=rules, root=tmp_path)
    queue.stop()


def login(case, username="admin"):
    case.client.cookies.clear()
    case.client.get("/login")
    response = case.client.post("/login", data={"username": username, "password": PASSWORD,
                               "csrf_token": case.client.cookies[LOGIN_CSRF_COOKIE]}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return case.client.get("/v1/session").json()["csrf_token"]


def import_draft(case, csrf, payload=None, *, file=False):
    text = json.dumps(draft() if payload is None else payload)
    kwargs = {"data": {"reason": "Source reviewed for isolated test draft"}}
    if file:
        kwargs["files"] = {"rule_file": ("draft.json", text.encode(), "application/json")}
    else:
        kwargs["data"]["rule_json"] = text
    return case.client.post("/admin/rules/import", headers={"X-CSRF-Token": csrf},
                            follow_redirects=False, **kwargs)


def activate(case, csrf, version="admin-test-2", expected="admin-test-1"):
    return case.client.post("/admin/rules/activate", headers={"X-CSRF-Token": csrf},
                            data={"version": version, "expected_active": expected,
                                  "reason": "Reviewed source; select for new inspections"},
                            follow_redirects=False)


def retained(case, version):
    with case.repo._connect() as conn:
        row = conn.execute("SELECT * FROM rule_version WHERE version=?", (version,)).fetchone()
    return dict(row) if row else None


def test_bundled_pack_validates_without_losing_any_rule():
    pack = RulesEngine.from_directory().pack
    assert parse_rule_pack(pack.model_dump_json()) == pack


@pytest.mark.parametrize("expression", [
    {"execute": "print('unsafe')"}, {"all": []}, {"present": 42},
    {"present": "$decl.__class__"}, {"present": "$unknown.name"},
    {"present": "$pkg.not_a_field"}, {"present": "$decl.typo"},
    {"present": "$decl.net_quantity.model_dump"}, {"present": "$ctx.typo"},
    {"eq": [1]}, {"eq": [1, 2], "present": "$decl.net_quantity"},
    {"eq": [10 ** 1000, 1]},
    {"matches": ["$decl.net_quantity", "(a+)+$"]},
    {"matches": ["$decl.net_quantity", "a{10000}"]},
    {"matches": ["$decl.net_quantity", "a{1,99}a{1,99}"]},
    {"matches": ["$decl.net_quantity", "["]},
    {"rounded_to": ["$decl.net_quantity", 0]},
    {"approx_eq": [1, 2, -1]}, {"contains_phrase": ["$decl.net_quantity", []]},
    {"script_coverage": ["net_quantity", []]}, {"panel_is": ["net_quantity", "nonexistent"]},
    {"in": [1, [{"execute": "x"}]]}, {"tier_at_least": "Z"},
    {"value_of": "$decl.net_quantity"}, {"divide": [1, 0]},
    {"eq": [{"table_lookup": {"key": "$pkg.pdp_area_cm2", "rows": [
        {"max": 100, "value": 2}, {"max": 50, "value": 3}]}}, 1]},
    {"eq": [{"table_lookup": {"key": "$pkg.pdp_area_cm2", "rows": [
        {"max": None, "value": 2}, {"max": 100, "value": 3}]}}, 1]},
    {"eq": [{"table_lookup": {"key": "$pkg.pdp_area_cm2", "rows": [
        {"max": None, "value": True}]}}, 1]},
])
def test_unsupported_or_malformed_dsl_is_rejected(expression):
    with pytest.raises((ValueError, TypeError)):
        validate_expression(expression)


def test_dsl_depth_size_and_duplicate_json_keys_are_bounded():
    deep = True
    for _ in range(22):
        deep = {"not": deep}
    with pytest.raises(ValueError, match="depth"):
        validate_expression(deep)
    with pytest.raises(ValueError, match="node"):
        validate_expression({"all": [{"all": [True] * 100}] * 31})
    with pytest.raises(ValueError, match="512 KB"):
        parse_rule_pack(" " * (MAX_PACK_BYTES + 1))
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        parse_rule_pack('{"version":"a","version":"b"}')
    with pytest.raises(ValueError, match="Non-finite"):
        parse_rule_pack('{"number":NaN}')


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(unknown=True),
    lambda p: p.update(version="../bad"),
    lambda p: p.update(source="file:///secret"),
    lambda p: p.update(source="https://user:password@example.org"),
    lambda p: p.update(notes=""),
    lambda p: p.update(rules=[]),
    lambda p: p["rules"].append(copy.deepcopy(p["rules"][0])),
    lambda p: p["rules"][0].update(typo=True),
    lambda p: p["rules"][0]["citation"].update(unknown=True),
    lambda p: p["rules"][0].update(messages={"unknown": "text"}),
    lambda p: p["rules"][0].update(effective_to="2019-01-01"),
    lambda p: p["rules"][0].update(effective_from=0),
    lambda p: p["rules"][0].update(assertion=False),
    lambda p: p["rules"][0].pop("assert"),
])
def test_strict_schema_rejects_silent_field_loss(mutate):
    payload = draft()
    mutate(payload)
    with pytest.raises((ValueError, TypeError)):
        parse_rule_pack(json.dumps(payload))


@pytest.mark.parametrize("expression", [
    {"all": [{"present": "$decl.net_quantity"}, {"not": False}]},
    {"eq": [{"divide": [100, 20]}, 5]},
    {"matches": ["$decl.net_quantity.raw", r"^\d{2} g$"]},
    {"gte_measured": ["$meas.net_quantity_cap_height", {"table_lookup": {
        "key": "$pkg.pdp_area_cm2", "boundary_policy": "favour_subject",
        "select": {"if": "$pkg.is_blown_formed", "then": "blown", "else": "normal"},
        "rows": [{"max": 100, "blown": 3, "normal": 2},
                 {"max": None, "blown": 4, "normal": 3}],
    }}]},
])
def test_supported_dsl_survives_import_without_expression_changes(expression):
    payload = draft()
    payload["rules"][0]["assert"] = expression
    assert parse_rule_pack(json.dumps(payload)).rules[0].assertion == expression


@pytest.mark.parametrize("check", [
    {"kind": "unknown"}, {"kind": "price_uniqueness", "ignored": True},
    {"kind": "consumer_care", "current_from": "20260101",
     "current_fields": ["name"], "historical_fields": ["phone"]},
    {"kind": "consumer_care", "current_from": "2026-01-01",
     "current_fields": [], "historical_fields": ["phone"]},
    {"kind": "date", "manufacture_from": "2024-01-01",
     "manufacture_pattern": "(a+)+$", "review_categories": []},
    {"kind": "unit_price", "exempt_bundles": [], "bases": {}},
    {"kind": "manufacturer", "review_categories": ["made_up_category"]},
])
def test_import_validates_legal_screen_parameters_and_metadata(check):
    payload = draft()
    payload["rules"][0]["legal_check"] = check
    with pytest.raises((ValueError, TypeError)):
        parse_rule_pack(json.dumps(payload))
    payload["rules"][0]["legal_check"] = {}
    payload["rules"][0]["assert"] = {"legal_screen": check}
    with pytest.raises((ValueError, TypeError)):
        parse_rule_pack(json.dumps(payload))


def test_import_rejects_unknown_nested_policy_and_source_fields():
    payload = draft()
    payload["legal_policy"] = {"pretend_certification": True}
    with pytest.raises(ValueError):
        parse_rule_pack(json.dumps(payload))
    payload.pop("legal_policy")
    payload["rules"][0]["sources"] = [{"url": "https://example.org", "clause": "1",
        "publication_date": "2026-01-01", "notification": "Test", "review_status": "draft",
        "unknown": "must not silently disappear"}]
    with pytest.raises(ValueError):
        parse_rule_pack(json.dumps(payload))


def test_admin_page_lists_metadata_and_working_forms(case):
    csrf = login(case)
    assert import_draft(case, csrf).status_code == 303
    response = case.client.get("/admin/rules")
    assert response.status_code == 200
    assert 'href="/admin/rules"' in response.text
    assert 'action="/admin/rules/import"' in response.text
    assert 'action="/admin/rules/activate"' in response.text
    assert 'name="expected_active" value="admin-test-1"' in response.text
    assert csrf in response.text and "data-review-form" in response.text
    assert "does not certify" in response.text
    listing = case.client.get("/v1/admin/rules").json()
    assert listing["active_version"] == "admin-test-1"
    assert len(listing["versions"]) == 2
    assert all("record" not in item for item in listing["versions"])


@pytest.mark.parametrize("username", ["inspector", "supervisor"])
def test_reads_and_writes_are_administrator_only(case, username):
    assert case.client.get("/admin/rules").status_code == 401
    assert case.client.get("/v1/admin/rules").status_code == 401
    csrf = login(case, username)
    for route in ("/admin/rules", "/v1/admin/rules", "/v1/admin/rules/admin-test-1"):
        assert case.client.get(route).status_code == 403
    assert import_draft(case, csrf).status_code == 403
    assert activate(case, csrf).status_code == 403
    assert retained(case, "admin-test-2") is None


def test_csrf_required_even_for_valid_admin_payloads(case):
    login(case)
    assert import_draft(case, "invalid").status_code == 403
    assert activate(case, "invalid").status_code == 403
    assert retained(case, "admin-test-2") is None


@pytest.mark.parametrize("file", [False, True])
def test_import_retains_content_and_actor_without_selecting_draft(case, file):
    csrf = login(case)
    response = import_draft(case, csrf, file=file)
    assert response.status_code == 303, response.text
    row = retained(case, "admin-test-2")
    assert row["sha256"] == hashlib.sha256(row["record"].encode()).hexdigest()
    assert row["actor_id"] == case.admin.id
    assert case.web.rules.pack.version == "admin-test-1"
    events = case.security.audit_events(entity_type="rule_version", entity_id="admin-test-2")
    assert len(events) == 1 and events[0]["action"] == "rules.draft_imported"
    assert events[0]["actor_id"] == case.admin.id
    response = case.client.get("/v1/admin/rules/admin-test-2")
    assert response.status_code == 200 and response.json()["version"] == "admin-test-2"
    assert hashlib.sha256(response.content).hexdigest() == row["sha256"]
    assert 'filename="tula-rules-admin-test-2.json"' in response.headers["content-disposition"]


def test_reimport_preserves_original_hash_and_note_but_content_collision_fails(case):
    csrf = login(case)
    assert import_draft(case, csrf).status_code == 303
    original = retained(case, "admin-test-2")
    assert import_draft(case, csrf).status_code == 303
    assert retained(case, "admin-test-2") == original
    payload = draft()
    payload["rules"][0]["assert"] = False
    response = import_draft(case, csrf, payload)
    assert response.status_code == 422
    assert retained(case, "admin-test-2") == original


def test_historical_raw_archive_is_preserved_across_new_model_defaults(case):
    legacy = draft("legacy-without-new-defaults")
    raw = json.dumps(legacy, indent=2)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with case.repo._connect() as conn:
        conn.execute("INSERT INTO rule_version VALUES (?,?,?,?,?,?)",
                     (legacy["version"], digest, raw, "2025-01-01", None, "Original raw record"))
    before = retained(case, legacy["version"])
    case.repo.archive_rules(parse_rule_pack(raw))
    assert retained(case, legacy["version"]) == before
    assert case.repo.archived_rules(legacy["version"]) == legacy


def test_http_malformed_dsl_and_upload_limits_leave_archive_unchanged(case):
    csrf = login(case)
    payload = draft()
    payload["rules"][0]["assert"] = {"unknown_operator": "unsafe"}
    response = import_draft(case, csrf, payload)
    assert response.status_code == 422 and "Unknown" in response.json()["detail"]
    response = case.client.post("/admin/rules/import", headers={"X-CSRF-Token": csrf},
        data={"reason": "Test oversized file"},
        files={"rule_file": ("large.json", b" " * (MAX_PACK_BYTES + 1))})
    assert response.status_code == 422
    response = case.client.post("/admin/rules/import", headers={"X-CSRF-Token": csrf},
        data={"reason": "Test invalid encoding"}, files={"rule_file": ("bad.json", b"\xff\xfe")})
    assert response.status_code == 422
    assert retained(case, "admin-test-2") is None


def test_import_and_activation_are_atomic_with_audit(case, monkeypatch):
    csrf = login(case)
    original_audit = case.security._audit

    def failing_audit(conn, **event):
        if event["action"].startswith("rules."):
            raise RuntimeError("Deliberate audit storage failure")
        return original_audit(conn, **event)

    monkeypatch.setattr(case.security, "_audit", failing_audit)
    assert import_draft(case, csrf).status_code == 500
    assert retained(case, "admin-test-2") is None
    monkeypatch.setattr(case.security, "_audit", original_audit)
    assert import_draft(case, csrf).status_code == 303
    monkeypatch.setattr(case.security, "_audit", failing_audit)
    assert activate(case, csrf).status_code == 500
    assert case.web.rules.pack.version == "admin-test-1"
    assert load_active_rules(case.repo, case.default).pack.version == "admin-test-1"


def test_activation_restart_conflict_and_rollback_preserve_all_versions(case):
    csrf = login(case)
    assert import_draft(case, csrf).status_code == 303
    original = retained(case, "admin-test-1")
    draft_row = retained(case, "admin-test-2")
    assert activate(case, csrf).status_code == 303
    assert case.web.rules.pack.version == "admin-test-2"
    fresh_repo = Repository(case.repo.path)
    assert load_active_rules(fresh_repo, case.default).pack.version == "admin-test-2"
    assert activate(case, csrf).status_code == 409
    assert activate(case, csrf, "admin-test-1", "admin-test-2").status_code == 303
    assert load_active_rules(fresh_repo, case.default).pack.version == "admin-test-1"
    assert retained(case, "admin-test-1") == original
    assert retained(case, "admin-test-2") == draft_row
    events = [e for e in case.security.audit_events(entity_type="rule_version")
              if e["action"] == "rules.activated"]
    assert len(events) == 2
    assert events[0]["before"] == {"active_version": "admin-test-2"}
    assert events[0]["after"]["active_version"] == "admin-test-1"
    assert events[0]["actor_id"] == case.admin.id


def test_queued_job_keeps_old_version_after_activation_and_worker_restart(case, monkeypatch):
    csrf = login(case)
    old = case.queue.enqueue([], {}, [], actor=case.admin, lane="field", complete=False)
    assert import_draft(case, csrf).status_code == 303
    assert activate(case, csrf).status_code == 303
    new = case.queue.enqueue([], {}, [], actor=case.admin, lane="field", complete=False)
    with case.repo._connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM inspection_job")}
    assert json.loads(rows[old["id"]]["payload"])["rules_version"] == "admin-test-1"
    assert json.loads(rows[new["id"]]["payload"])["rules_version"] == "admin-test-2"
    actual_versions = []

    def analysis_stub(captures, options, *, rules):
        actual_versions.append(rules.pack.version)
        return Analysis(scan=Scan(scan_id="queue-test"), package=PackageFacts(),
                        rules_version=rules.pack.version, engine="explicit queue test stub")

    monkeypatch.setattr(jobs_module, "analyse", analysis_stub)
    restarted = InspectionJobs(Repository(case.repo.path), load_active_rules(case.repo, case.default))
    restarted._run(rows[old["id"]])
    restarted._run(rows[new["id"]])
    assert actual_versions == ["admin-test-1", "admin-test-2"]
    for job, version in ((old, "admin-test-1"), (new, "admin-test-2")):
        saved = restarted.get(job["id"])
        assert saved["state"] == "complete"
        assert case.repo.get(saved["scan_id"]).rules_version == version
        assert case.repo.revision(saved["scan_id"], 0).rules_version == version


def test_missing_or_corrupt_selected_archive_fails_visibly(case):
    with case.repo._connect() as conn:
        conn.execute("INSERT INTO app_configuration VALUES (?,?,?,?,?)",
                     (ACTIVE_KEY, "missing", "2026-09-07", case.admin.id, "test failure"))
    with pytest.raises(ValueError, match="missing"):
        load_active_rules(case.repo, case.default)
    with case.repo._connect() as conn:
        conn.execute("UPDATE app_configuration SET value='admin-test-1'")
        conn.execute("UPDATE rule_version SET record='{}' WHERE version='admin-test-1'")
    with pytest.raises(ValueError):
        load_active_rules(case.repo, case.default)


def test_html_escapes_imported_metadata_and_rejects_ambiguous_upload(case):
    csrf = login(case)
    payload = draft()
    payload["title"] = '<script>alert("untrusted")</script>'
    assert import_draft(case, csrf, payload).status_code == 303
    page = case.client.get("/admin/rules").text
    assert '&lt;script&gt;' in page and '<script>alert("untrusted")</script>' not in page
    response = case.client.post("/admin/rules/import", headers={"X-CSRF-Token": csrf},
        data={"rule_json": json.dumps(draft("new")), "reason": "Test ambiguity"},
        files={"rule_file": ("draft.json", json.dumps(draft("other")).encode())})
    assert response.status_code == 422 and retained(case, "new") is None


def test_real_app_startup_loads_persisted_selection_and_installs_navigation(case):
    csrf = login(case)
    assert import_draft(case, csrf).status_code == 303
    assert activate(case, csrf).status_code == 303
    environment = dict(os.environ, TULA_DATA_DIR=str(case.root))
    code = ("import json; import tula.web.app as web; "
            "print(json.dumps({'active':web.rules.pack.version,"
            "'routes':[r.path for r in web.app.routes if 'admin/rules' in r.path]}))")
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout.strip().splitlines()[-1])
    assert output["active"] == "admin-test-2"
    assert {"/admin/rules", "/admin/rules/import", "/admin/rules/activate"} <= set(output["routes"])
