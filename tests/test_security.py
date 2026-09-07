"""Authentication is exercised through real cookies, persisted sessions and HTTP."""

import io
import sqlite3
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.testclient import TestClient

from tula.security import SecurityStore, User, install_security, require_permission
from tula.security.__main__ import main
from tula.security.store import (
    IDLE_SECONDS,
    SESSION_SECONDS,
    AuthenticationError,
    RateLimited,
    hash_password,
    verify_password,
)
from tula.security.web import LOGIN_CSRF_COOKIE, SESSION_COOKIE

PASSWORD = "A test passphrase 2026!"
Approver = Annotated[User, Depends(require_permission("approve"))]


@pytest.fixture
def store(tmp_path):
    security = SecurityStore(tmp_path / "tula.db")
    security.create_user("admin", PASSWORD, role="admin", bootstrap=True)
    return security


def admin(store):
    return next(user for user in store.list_users() if user.username == "admin")


def add_user(store, username="inspector", role="inspector"):
    return store.create_user(username, PASSWORD, role=role, actor_id=admin(store).id)


@pytest.fixture
def client(store):
    app = FastAPI()
    install_security(app, store.path)
    app.state.security = store

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/dashboard")
    def dashboard(request: Request):
        return {"username": request.state.user.username}

    @app.get("/v1/products/123/history")
    def history():
        return {"history": []}

    @app.post("/approve")
    def approve(user: Approver):
        return {"approved_by": user.username}

    @app.post("/inspect")
    async def inspect(file: UploadFile):
        return {"filename": file.filename, "length": len(await file.read())}

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def sign_in(client, username="admin", password=PASSWORD):
    response = client.get("/login")
    assert response.status_code == 200
    response = client.post("/login", data={
        "username": username, "password": password,
        "csrf_token": client.cookies[LOGIN_CSRF_COOKIE],
    }, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client.get("/v1/session").json()["csrf_token"]


def test_password_hash_has_random_salt_and_verifies():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong password", first)
    assert not verify_password(PASSWORD, "malformed")
    assert not verify_password(PASSWORD, first.replace("32768", "2147483648"))
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")


def test_migration_preserves_existing_inspections(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE inspection (scan_id TEXT PRIMARY KEY, record TEXT)")
        conn.execute("INSERT INTO inspection VALUES ('legacy', '{\"evidence\":true}')")
    SecurityStore(database)
    SecurityStore(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT record FROM inspection").fetchone()[0] == '{"evidence":true}'
        assert conn.execute("SELECT COUNT(*) FROM security_migration").fetchone()[0] == 1


def test_bootstrap_cannot_replace_existing_accounts(store):
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("intruder", PASSWORD, role="admin", bootstrap=True)
    assert len(store.list_users()) == 1


def test_usernames_unique_case_insensitive_and_sql_is_data(store):
    with pytest.raises(ValueError, match="already in use"):
        store.create_user("ADMIN", PASSWORD, actor_id=admin(store).id)
    with pytest.raises(ValueError, match="Username"):
        store.create_user("x'; DROP TABLE security_user; --", PASSWORD, actor_id=admin(store).id)
    with pytest.raises(AuthenticationError):
        store.login("admin' OR 1=1 --", PASSWORD)
    assert store.has_users()


def test_password_and_session_tokens_never_stored_plaintext(store):
    result = store.login("admin", PASSWORD)
    with sqlite3.connect(store.path) as conn:
        password_hash = conn.execute("SELECT password_hash FROM security_user").fetchone()[0]
        token_hash = conn.execute("SELECT token_hash FROM security_session").fetchone()[0]
    assert result.token != token_hash
    assert PASSWORD not in password_hash
    assert store.session(result.token).user.username == "admin"
    assert SecurityStore(store.path).session(result.token) is not None


def test_unknown_disabled_and_wrong_password_have_same_failure(store):
    user = add_user(store)
    store.update_user(user.id, actor_id=admin(store).id, role="inspector", active=False)
    messages = []
    for name, password in (("unknown", PASSWORD), ("admin", "bad"), ("inspector", PASSWORD)):
        with pytest.raises(AuthenticationError) as exc:
            store.login(name, password)
        messages.append(str(exc.value))
    assert len(set(messages)) == 1


def test_account_throttle_persists_across_store_restart_and_then_expires(store):
    clock = [100_000.0]
    store.clock = lambda: clock[0]
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            store.login("admin", "wrong", client_ip="1.2.3.4")
    restarted = SecurityStore(store.path, clock=store.clock)
    with pytest.raises(RateLimited) as exc:
        restarted.login("admin", PASSWORD, client_ip="5.6.7.8")
    assert 1 <= exc.value.retry_after <= 901
    clock[0] += 901
    assert restarted.login("admin", PASSWORD).session.user.username == "admin"


def test_ip_throttle_blocks_rotating_usernames(store):
    with store._connect(write=True) as conn:
        bucket = store._buckets("unused", "attacker")[1][0]
        conn.execute("INSERT INTO security_login_attempt VALUES (?,40,?)", (bucket, store.clock()))
    with pytest.raises(RateLimited):
        store.login("fresh-username", PASSWORD, client_ip="attacker")


def test_absolute_expiry_and_idle_timeout_are_enforced(store):
    clock = [100_000.0]
    store.clock = lambda: clock[0]
    result = store.login("admin", PASSWORD)
    clock[0] += IDLE_SECONDS
    assert store.session(result.token) is None
    result = store.login("admin", PASSWORD)
    for _ in range(SESSION_SECONDS // 600):
        clock[0] += 600
        session = store.session(result.token)
    assert session is None


def test_role_change_deactivation_and_password_reset_revoke_sessions(store):
    user = add_user(store)
    login = store.login(user.username, PASSWORD)
    store.update_user(user.id, actor_id=admin(store).id, role="supervisor", active=True)
    assert store.session(login.token) is None
    login = store.login(user.username, PASSWORD)
    assert login.session.user.can("approve")
    store.change_password(user.id, "Another secure passphrase!", actor_id=admin(store).id)
    assert store.session(login.token) is None
    login = store.login(user.username, "Another secure passphrase!")
    store.update_user(user.id, actor_id=admin(store).id, role="supervisor", active=False)
    assert store.session(login.token) is None


def test_last_administrator_and_self_lockout_are_prevented(store):
    original = admin(store)
    with pytest.raises(ValueError, match="another administrator"):
        store.update_user(original.id, actor_id=original.id, role="inspector", active=True)
    with pytest.raises(ValueError, match="another administrator"):
        store.update_user(original.id, actor_id=original.id, role="admin", active=False)
    # The service checks the actor, even if a route forgets its dependency.
    inspector = add_user(store)
    with pytest.raises(ValueError, match="active administrator"):
        store.update_user(original.id, actor_id=inspector.id, role="admin", active=False)
    with pytest.raises(ValueError, match="active administrator"):
        store.create_user("escalated", PASSWORD, role="admin", actor_id=inspector.id)
    with pytest.raises(ValueError, match="active administrator"):
        store.change_password(original.id, "Attacker chosen password", actor_id=inspector.id)


def test_self_password_change_requires_current_password_and_revokes_sessions(store):
    user = admin(store)
    login = store.login(user.username, PASSWORD)
    with pytest.raises(ValueError, match="current password"):
        store.change_password(user.id, "My new secure password", actor_id=user.id,
                              current_password="incorrect")
    assert store.session(login.token) is not None
    store.change_password(user.id, "My new secure password", actor_id=user.id,
                          current_password=PASSWORD)
    assert store.session(login.token) is None


def test_audit_before_after_are_persisted_redacted_and_append_only(store):
    actor = admin(store)
    store.audit(actor_id=actor.id, action="finding.verified", entity_type="inspection", entity_id="scan-1",
                before={"verified": False, "password": "do not store"}, after={"verified": True})
    event = store.audit_events(entity_id="scan-1")[0]
    assert event["actor_username"] == "admin"
    assert event["before"] == {"verified": False, "password": "[redacted]"}
    assert event["after"] == {"verified": True}
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM security_audit")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE security_audit SET action='forged'")


@pytest.mark.parametrize("path", ["/dashboard", "/v1/products/123/history", "/docs", "/openapi.json", "/v1/admin/users", "/admin/audit", "/inspections/secret/report.pdf", "/inspections/secret/frames/0"])
def test_unauthenticated_routes_and_apis_are_protected(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert "Cache-Control" in response.headers
    assert "no-store" in response.headers["Cache-Control"]


def test_browser_redirects_to_login_but_health_is_public(client):
    response = client.get("/dashboard", headers={"Accept": "text/html"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert client.get("/healthz").status_code == 200


def test_login_csrf_and_cookie_security(client):
    assert client.post("/login", data={"username": "admin", "password": PASSWORD}).status_code == 403
    page = client.get("/login")
    assert "Secure" in page.headers["set-cookie"]
    assert "HttpOnly" in page.headers["set-cookie"]
    assert "SameSite=strict" in page.headers["set-cookie"]
    response = client.post("/login", data={"username": "admin", "password": PASSWORD,
                                           "csrf_token": client.cookies[LOGIN_CSRF_COOKIE],
                                           "next": "https://attacker.example"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    session_cookie = response.headers.get_list("set-cookie")[0]
    for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert attribute in session_cookie
    assert client.get("/dashboard").json()["username"] == "admin"


def test_csrf_binds_to_session_and_rejects_cross_origin(client, store):
    csrf = sign_in(client)
    assert client.post("/approve").status_code == 403
    assert client.post("/approve", headers={"X-CSRF-Token": "incorrect"}).status_code == 403
    assert client.post("/approve", headers=[(b"X-CSRF-Token", b"\xff")]).status_code == 403
    another_session = store.login("admin", PASSWORD)
    assert client.post("/approve", headers={"X-CSRF-Token": another_session.session.csrf_token}).status_code == 403
    assert client.post("/approve", headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/approve", headers={"X-CSRF-Token": csrf, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/approve", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_multipart_body_is_preserved_and_requires_csrf_header(client):
    csrf = sign_in(client)
    assert client.post("/inspect", files={"file": ("sample.png", b"image bytes")}).status_code == 403
    response = client.post("/inspect", files={"file": ("sample.png", b"image bytes")},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {"filename": "sample.png", "length": 11}


def test_inspector_cannot_manage_users_or_approve(client, store):
    add_user(store)
    csrf = sign_in(client, "inspector")
    assert client.get("/admin/users").status_code == 403
    assert client.get("/v1/admin/audit").status_code == 403
    assert client.post("/admin/users", data={"csrf_token": csrf}).status_code == 403
    assert client.post("/approve", headers={"X-CSRF-Token": csrf}).status_code == 403
    assert client.get("/dashboard").status_code == 200


def test_supervisor_can_approve_but_cannot_manage_users(client, store):
    add_user(store, "supervisor", "supervisor")
    csrf = sign_in(client, "supervisor")
    assert client.post("/approve", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/admin/users").status_code == 403


def test_admin_can_create_change_and_reset_user_via_real_forms(client, store):
    csrf = sign_in(client)
    response = client.post("/admin/users", data={"csrf_token": csrf, "username": "new-officer",
                                                "password": PASSWORD, "role": "inspector",
                                                "display_name": '<script>alert(1)</script>'})
    assert response.status_code == 200
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
    assert '<script>alert(1)</script>' not in response.text
    user = next(user for user in store.list_users() if user.username == "new-officer")
    response = client.post(f"/admin/users/{user.id}", data={"csrf_token": csrf, "role": "supervisor",
                                                          "active": "1", "display_name": "Officer"})
    assert response.status_code == 200
    assert store.get_user(user.id).role == "supervisor"
    response = client.post(f"/admin/users/{user.id}/password", data={"csrf_token": csrf,
                                                                   "new_password": "A different passphrase"})
    assert response.status_code == 200
    assert store.login("new-officer", "A different passphrase").session.user.id == user.id
    events = client.get("/v1/admin/audit").json()["events"]
    assert {"user.created", "user.updated", "user.password_changed"}.issubset({event["action"] for event in events})


def test_logout_csrf_and_old_cookie_revocation(client, store):
    csrf = sign_in(client)
    token = client.cookies[SESSION_COOKIE]
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 403
    assert client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    assert store.session(token) is None
    assert client.get("/dashboard").status_code == 401


def test_remote_plaintext_http_is_rejected_even_with_loopback_host_header(client):
    response = client.get("http://remote.example/login")
    assert response.status_code == 426
    assert client.get("http://127.0.0.1/login").status_code == 426  # peer is testclient, not loopback


def test_same_origin_submission_works_when_app_has_url_prefix(store):
    app = FastAPI(root_path="/tula")
    install_security(app, store.path)
    with TestClient(app, base_url="https://testserver") as client:
        client.get("/login")
        response = client.post("/login", data={"username": "admin", "password": PASSWORD,
                                              "csrf_token": client.cookies[LOGIN_CSRF_COOKIE]},
                               headers={"Origin": "https://testserver"}, follow_redirects=False)
        assert response.status_code == 303


def test_real_loopback_http_is_supported(store):
    app = FastAPI()
    install_security(app, store.path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert "Secure" not in response.headers["set-cookie"]


def test_bootstrap_cli_reads_password_without_printing_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    database = tmp_path / "bootstrap.db"
    args = ["bootstrap", "--database", str(database), "--username", "first-admin", "--password-stdin"]
    assert main(args) == 0
    assert PASSWORD not in capsys.readouterr().out
    assert SecurityStore(database).login("first-admin", PASSWORD).session.user.role == "admin"
    assert main(args) == 2
