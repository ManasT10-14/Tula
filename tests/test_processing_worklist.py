"""Saved queue discovery must use persisted jobs and enforce account scope.

Seeded queue rows are explicit test fixtures. Recognition and retry execution
are covered by the existing capture/worker tests; this file tests discovery.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from tula.rules.engine import RulesEngine
from tula.security import SecurityStore, User, install_security
from tula.security.web import LOGIN_CSRF_COOKIE
from tula.services.jobs import InspectionJobs
from tula.storage.db import Repository
from tula.web.workflow import install_workflow

OWNER = User("owner", "owner", "Owner Inspector", "inspector", True)
OTHER = User("other", "other", "Other Inspector", "inspector", True)
SUPERVISOR = User("supervisor", "supervisor", "Supervisor", "supervisor", True)
PASSWORD = "Worklist test account passphrase 2026!"
STATES = ("queued", "running", "failed", "complete")


def seed(queue, actor, number, state="queued", **metadata):
    job_id = f"{number:032x}"
    payload = {"operator": actor.display_name, "region": "QA location", "lane": "field",
               "captures": [{"path": "private-evidence-path.png"}],
               "hashes": {"private-original.bin": "private-hash"}, **metadata}
    with queue.repo._connect() as conn:
        conn.execute("INSERT INTO inspection_job "
            "(id,owner_id,state,stage,detail,payload,created_at,updated_at,scan_id,error,attempts,lease_token) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, actor.id, state, "ocr", "Last saved OCR stage",
            json.dumps(payload), "2026-09-07T10:00:00+00:00", 1788765600,
            f"QA-{number}" if state == "complete" else None,
            "Retake the blurred marking." if state == "failed" else None, 1, "private-lease-token"))
    return job_id


@pytest.fixture
def queue(tmp_path, monkeypatch):
    queue = InspectionJobs(Repository(tmp_path / "tula.db"), RulesEngine.from_directory())
    monkeypatch.setattr(queue, "start", lambda: True)
    for offset, actor in ((0, OWNER), (10, OTHER)):
        for i, state in enumerate(STATES, 1):
            seed(queue, actor, offset + i, state)
    return queue


def test_inspector_list_and_totals_exclude_every_other_owner(queue):
    result = queue.list_for(OWNER)
    assert result["counts"] == dict.fromkeys(STATES, 1)
    assert result["total"] == 3
    assert {row["owner_id"] for row in result["rows"]} == {OWNER.id}
    assert {row["state"] for row in result["rows"]} == set(STATES) - {"complete"}
    assert all(row["image_count"] == 1 for row in result["rows"])


@pytest.mark.parametrize("role", ["supervisor", "admin"])
def test_privileged_scope_includes_actual_jobs_and_counts(queue, role):
    actor = SimpleNamespace(id="privileged", role=role)
    result = queue.list_for(actor, state="all")
    assert result["total"] == 8 and result["counts"] == dict.fromkeys(STATES, 2)
    assert {row["owner_id"] for row in result["rows"]} == {OWNER.id, OTHER.id}


@pytest.mark.parametrize("state,count", [("unfinished", 3), ("all", 4), *[(s, 1) for s in STATES]])
def test_state_filters_do_not_change_scope_totals(queue, state, count):
    result = queue.list_for(OWNER, state=state)
    assert result["total"] == count and len(result["rows"]) == count
    assert result["counts"] == dict.fromkeys(STATES, 1)


def test_discovery_never_returns_payload_or_private_evidence_and_lease_fields(queue):
    serialized = json.dumps(queue.list_for(SUPERVISOR, state="all"))
    for forbidden in ("payload", "private-evidence", "private-original", "private-hash", "lease_token", "private-lease"):
        assert forbidden not in serialized


def test_pagination_is_stable_bounded_and_clamps_to_last_page(queue):
    for number in range(100, 131):
        seed(queue, OWNER, number)
    first = queue.list_for(OWNER, state="all")
    second = queue.list_for(OWNER, state="all", page=2)
    assert first["total"] == second["total"] == 35
    assert len(first["rows"]) == 25 and len(second["rows"]) == 10
    ids = [row["id"] for result in (first, second) for row in result["rows"]]
    assert len(set(ids)) == len(ids)
    assert queue.list_for(OWNER, state="all", page=9999)["rows"] == second["rows"]
    assert queue.list_for(OWNER, state="all")["rows"] == first["rows"]


def test_all_job_pages_use_scope_specific_recency_indexes(queue):
    with queue.repo._connect() as conn:
        privileged = [row["detail"] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM inspection_job "
            "ORDER BY created_at DESC,id LIMIT 25"
        )]
        inspector = [row["detail"] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM inspection_job WHERE owner_id=? "
            "ORDER BY created_at DESC,id LIMIT 25",
            (OWNER.id,),
        )]
    assert any("idx_job_created" in detail for detail in privileged)
    assert any("idx_job_owner_created" in detail for detail in inspector)
    assert all("TEMP B-TREE" not in detail for detail in (*privileged, *inspector))


@pytest.mark.parametrize("kwargs", [{"state": "failed' OR 1=1 --"}, {"page": 0}, {"page": -1},
    {"page": True}, {"page": 1.5}, {"page": 1_000_001}, {"page_size": 0}, {"page_size": 101}])
def test_bad_filters_are_rejected(queue, kwargs):
    with pytest.raises(ValueError):
        queue.list_for(OWNER, **kwargs)


@pytest.mark.parametrize("payload", ["not JSON", "null", "[]", '{"operator":{},"captures":12}'])
def test_damaged_historical_metadata_does_not_hide_job_state(queue, payload):
    with queue.repo._connect() as conn:
        conn.execute("UPDATE inspection_job SET payload=? WHERE id=?", (payload, f"{1:032x}"))
    first = queue.list_for(OWNER)["rows"][0]
    assert first["state"] == "queued" and first["operator"] == "" and first["image_count"] is None


@pytest.fixture
def web_case(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "web.db")
    security = SecurityStore(repo.path)
    admin = security.create_user("admin", PASSWORD, role="admin", bootstrap=True)
    owner = security.create_user("owner", PASSWORD, role="inspector", actor_id=admin.id)
    other = security.create_user("other", PASSWORD, role="inspector", actor_id=admin.id)
    supervisor = security.create_user("supervisor", PASSWORD, role="supervisor", actor_id=admin.id)
    app = FastAPI()
    install_security(app, repo.path)
    app.state.security = security
    rules = RulesEngine.from_directory()
    queue = InspectionJobs(repo, rules, security)
    monkeypatch.setattr(queue, "start", lambda: True)
    app.state.jobs = queue
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "src/tula/web/templates"))
    templates.env.globals["asset_url"] = lambda name: "/static/" + name
    web = SimpleNamespace(app=app, repo=repo, rules=rules, templates=templates, UPLOADS=tmp_path / "uploads")
    install_workflow(web)
    own_id = seed(queue, owner, 1, "failed", operator='<script>alert("owner")</script>')
    other_id = seed(queue, other, 2, "running", operator="Other private inspector")
    return SimpleNamespace(app=app, queue=queue, owner=owner, other=other, supervisor=supervisor,
                           own_id=own_id, other_id=other_id)


def login(client, name):
    client.get("/login")
    response = client.post("/login", data={"username": name, "password": PASSWORD,
        "csrf_token": client.cookies[LOGIN_CSRF_COOKIE]}, follow_redirects=False)
    assert response.status_code == 303
    return client.get("/v1/session").json()["csrf_token"]


def test_worklist_http_auth_scope_and_escaped_html(web_case):
    case = web_case
    with TestClient(case.app, base_url="https://testserver") as client:
        assert client.get("/v1/jobs").status_code == 401
        assert client.get("/processing", headers={"Accept": "text/html"}, follow_redirects=False).status_code == 303
        login(client, "owner")
        for query in ("", f"?state=all&owner_id={case.other.id}"):
            response = client.get("/v1/jobs" + query)
            assert response.status_code == 200
            assert [row["id"] for row in response.json()["rows"]] == [case.own_id]
            assert case.other_id not in response.text
        html = client.get("/processing").text
        assert "Saved processing jobs" in html and "Retake the blurred marking." in html
        assert f'/?job={case.own_id}' in html and case.other_id not in html
        assert "Other private inspector" not in html and "private-evidence-path" not in html
        assert '<script>alert("owner")</script>' not in html and "&lt;script&gt;" in html
        client.cookies.clear()
        login(client, "supervisor")
        assert client.get("/v1/jobs").json()["total"] == 2
        assert "Showing jobs from all accounts" in client.get("/processing").text


def test_worklist_links_reuse_protected_live_progress_and_retry(web_case):
    case = web_case
    with TestClient(case.app, base_url="https://testserver") as client:
        token = login(client, "other")
        assert client.get(f"/v1/jobs/{case.own_id}").status_code == 404
        assert client.post(f"/v1/jobs/{case.own_id}/retry", headers={"X-CSRF-Token": token}).status_code == 404
        client.cookies.clear()
        token = login(client, "owner")
        assert client.get(f"/v1/jobs/{case.own_id}").json()["state"] == "failed"
        assert client.post(f"/v1/jobs/{case.own_id}/retry").status_code == 403
        assert client.post(f"/v1/jobs/{case.own_id}/retry", headers={"X-CSRF-Token": token}).status_code == 200
        assert client.get("/v1/jobs?state=failed").json()["total"] == 0
        assert client.get("/v1/jobs?state=queued").json()["total"] == 1


def test_http_invalid_filters_empty_state_and_complete_link(web_case):
    case = web_case
    with TestClient(case.app, base_url="https://testserver") as client:
        login(client, "owner")
        for query in ("?state=unknown", "?page=0", "?page=letters"):
            assert client.get("/v1/jobs" + query).status_code == 422
            assert client.get("/processing" + query).status_code == 422
        assert "No jobs match this view" in client.get("/processing?state=complete").text
        seed(case.queue, case.owner, 3, "complete")
        response = client.get("/processing?state=complete")
        assert response.status_code == 200 and '/inspections/QA-3' in response.text
