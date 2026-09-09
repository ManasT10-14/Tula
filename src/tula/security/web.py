"""Default-deny authentication middleware and a small accessible account console."""

from __future__ import annotations

import hmac
import ipaddress
import secrets
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from .store import SESSION_SECONDS, AuthenticationError, RateLimited, SecurityStore, User

SESSION_COOKIE = "tula_session"
LOGIN_CSRF_COOKIE = "tula_login_csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# /sw.js is the installed console's service worker. It has to be fetchable before
# sign-in for the app to install at all, and it carries no data: it caches only
# /static/, which is public already.
PUBLIC_PATHS = frozenset({"/login", "/healthz", "/sw.js"})
_MAX_FORM_BYTES = 64 * 1024
_MAX_BODY_BYTES = 302 * 1024 * 1024


def get_security(request: Request) -> SecurityStore:
    return request.app.state.security


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


def require_roles(*roles: str):
    def dependency(request: Request) -> User:
        user = current_user(request)
        if user.role not in roles:
            raise HTTPException(403, "Your account does not have permission for this action.")
        return user
    return dependency


def require_permission(permission: str):
    def dependency(request: Request) -> User:
        user = current_user(request)
        if not user.can(permission):
            raise HTTPException(403, "Your account does not have permission for this action.")
        return user
    return dependency


def _client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For here. A deployment proxy must be explicitly
    # trusted in Uvicorn's forwarded-allow-ips configuration.
    return request.client.host if request.client else "unknown"


def _local_http(request: Request) -> bool:
    if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        return ipaddress.ip_address(_client_ip(request)).is_loopback
    except ValueError:
        return False


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or not _local_http(request)


def _next(value: str) -> str:
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(c) < 32 for c in value)):
        return "/dashboard"
    return value


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        scope.setdefault("state", {})
        store = request.app.state.security

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=(self)"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; img-src 'self' data: blob:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                    "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
                )
                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "no-store, private"
                if request.url.scheme == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        async def reject(status: int, detail: str):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secured_send)

        if (path != "/healthz" and not path.startswith("/static/")
                and request.url.scheme != "https" and not _local_http(request)):
            await reject(426, "Use HTTPS to sign in or access inspection data. Local demonstrations may use loopback HTTP.")
            return
        session = await run_in_threadpool(store.session, request.cookies.get(SESSION_COOKIE))
        scope["state"]["user"] = session.user if session else None
        scope["state"]["csrf_token"] = session.csrf_token if session else ""
        public = path in PUBLIC_PATHS or path.startswith("/static/")
        if not public and not session:
            if request.method in {"GET", "HEAD"} and "text/html" in request.headers.get("accept", ""):
                destination = quote(path + ("?" + request.url.query if request.url.query else ""), safe="")
                response = RedirectResponse("/login?next=" + destination, status_code=303)
            else:
                response = JSONResponse({"detail": "Sign in to access inspections."}, status_code=401)
                if request.headers.get("HX-Request") == "true":
                    response.headers["HX-Redirect"] = "/login"
            await response(scope, receive, secured_send)
            return
        if path.startswith(("/admin", "/v1/admin")) and (not session or session.user.role != "admin"):
            await reject(403, "Administrator access is required.")
            return

        body_size = 0

        async def limited_receive():
            nonlocal body_size
            message = await receive()
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > _MAX_BODY_BYTES:
                    raise HTTPException(413, "The combined upload is too large. Upload at most 12 images of 25 MB each.")
            return message

        downstream_receive = limited_receive
        if request.method not in SAFE_METHODS:
            if request.headers.get("sec-fetch-site") == "cross-site":
                await reject(403, "Cross-site submissions are not allowed. Reload this page and try again.")
                return
            origin = request.headers.get("origin")
            address = urlsplit(str(request.url))
            if origin and origin.rstrip("/") != f"{address.scheme}://{address.netloc}":
                await reject(403, "The submission origin did not match this application.")
                return
            provided = request.headers.get("X-CSRF-Token", "")
            if not provided and request.headers.get("content-type", "").split(";")[0] == "application/x-www-form-urlencoded":
                chunks = []
                size = 0
                while True:
                    message = await limited_receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > _MAX_FORM_BYTES:
                        await reject(413, "This form is too large.")
                        return
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
                body = b"".join(chunks)
                try:
                    fields = parse_qs(body.decode("utf-8"), max_num_fields=100)
                    provided = fields.get("csrf_token", [""])[0]
                except (UnicodeDecodeError, ValueError):
                    await reject(400, "This form could not be read. Reload the page and try again.")
                    return
                delivered = False

                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await limited_receive()

                downstream_receive = replay
            expected = request.cookies.get(LOGIN_CSRF_COOKIE, "") if path == "/login" else (
                session.csrf_token if session else ""
            )
            if not expected or not provided or not hmac.compare_digest(
                provided.encode("utf-8"), expected.encode("utf-8")
            ):
                await reject(403, "Your form session expired or its security token is missing. Reload the page and try again.")
                return
        await self.app(scope, downstream_receive, secured_send)


def _page(title: str, body: str, user: User | None = None) -> HTMLResponse:
    """Account pages in the console's own design.

    These used to carry a separate palette and typeface, so signing in or
    opening Administration threw the officer into what looked like a different
    product. They now load the console stylesheet and only add the few rules
    this plain-HTML shell needs on top of it.
    """
    navigation = ('<a href="/dashboard">Dashboard</a><a href="/">New inspection</a>'
                  '<a href="/repository">Inspection records</a>') if user else ""
    if user and user.role == "admin":
        navigation += '<a href="/admin/users">Accounts</a><a href="/admin/audit">Audit log</a>'
    return HTMLResponse('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + escape(title) + ''' · TATVA</title>
<link rel="manifest" href="/static/manifest.webmanifest">
<meta name="theme-color" content="#f8fafc">
<link rel="icon" href="/static/favicon.png" type="image/png">
<link rel="apple-touch-icon" href="/static/favicon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="TATVA">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/console.css">
<script>
/* Sign-in is the first page an officer reaches, so the install prompt and the
   shell cache have to be available here and not only behind authentication.
   console.js is not loaded on these plain pages; this is the same registration. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {});
  });
}
</script>
<style>
header.auth{background:rgba(255,255,255,0.85);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);color:var(--chrome-text-hover);padding:18px 36px;display:flex;align-items:center;
  gap:32px;flex-wrap:wrap;border-bottom:1px solid var(--border-subtle);position:sticky;top:0;z-index:50}
header.auth strong{font-family:var(--font-sans);font-size:22px;font-weight:800;letter-spacing:-0.5px;color:var(--text-primary)}
header.auth nav{display:flex;gap:24px;flex-wrap:wrap;font-size:14px;font-weight:500}
header.auth a{color:var(--text-secondary);text-decoration:none;padding:6px 12px;border-radius:var(--radius-md);transition:all 0.2s ease;margin-left:-12px}
header.auth a:hover{color:var(--blue-700);background:var(--blue-50)}
main{max-width:1240px;margin:0 auto;padding:40px 36px 80px}
.narrow{max-width:460px;margin:8vh auto}
.card{margin:24px 0;background:var(--surface-default);border:1px solid var(--border-subtle);border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);padding:24px}
.card.table-wrap{padding:0;overflow:hidden}
.table-wrap table{width:100%;border-collapse:collapse}
.table-wrap th{background:rgba(248,250,252,0.85);color:var(--text-muted);font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;padding:14px 20px;border-bottom:1px solid var(--border-subtle);text-align:left}
.table-wrap td{padding:18px 20px;border-bottom:1px solid var(--border-subtle);color:var(--text-primary);font-size:14px;vertical-align:top}
.table-wrap tr:last-child td{border-bottom:none}
.table-wrap tbody tr:hover{background-color:var(--surface-subtle)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:20px}
.error{background:var(--status-danger-bg);color:var(--status-danger-text);border-left:3px solid var(--status-danger-border);padding:12px 15px;
  border-radius:0 var(--radius-md) var(--radius-md) 0;font-size:13px}
.success{background:var(--status-success-bg);color:var(--status-success-text);padding:12px 15px;border-radius:var(--radius-md);font-size:13px}
.table-wrap{overflow-x:auto}
td input,td select{min-width:115px;margin-bottom:16px}
td form label{margin-top:16px}
td form label:first-of-type{margin-top:0}
form button{margin-top:14px;width:100%}
td button,form.row button{margin-top:0}
label:has(input[type="checkbox"]){display:inline-flex;align-items:center;gap:8px;margin:8px 0 0;color:var(--text-primary);cursor:pointer;font-weight:600}
small{display:block;color:var(--text-muted);margin:6px 0;font-size:12.5px;line-height:1.4}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:450px}
code{overflow-wrap:anywhere}
footer{max-width:1240px;margin:0 auto;padding:18px 36px 40px;color:var(--text-muted);font-size:12px}
</style></head><body><header class="auth"><img src="/static/logo.png" alt="TATVA" style="height: 36px; width: auto; object-fit: contain;"><nav aria-label="Account navigation">''' + navigation + '''</nav></header><main>''' + body + '''</main><footer>Legal Metrology Inspection Platform · Evidence, review and accountable decisions</footer></body></html>''')


def _hidden(token: str) -> str:
    return '<input type="hidden" name="csrf_token" value="' + escape(token, quote=True) + '">'


def _login_page(request: Request, *, error: str = "", status: int = 200) -> Response:
    token = secrets.token_urlsafe(32)
    next_path = _next(request.query_params.get("next", "/dashboard"))
    setup = ""
    if not get_security(request).has_users():
        setup = '<p class="muted">This installation needs its first administrator. Ask the deployment administrator to complete account setup using the documented bootstrap command.</p>'
    body = '<div class="narrow"><p class="eyebrow">Authorized personnel</p><h1>Sign in to TATVA</h1><p class="muted">Review evidence, conduct inspections and maintain a traceable record of every decision.</p>'
    if error:
        body += '<p class="error" role="alert">' + escape(error) + '</p>'
    body += setup + '<form class="card" method="post" action="/login">' + _hidden(token)
    body += '<input type="hidden" name="next" value="' + escape(next_path, quote=True) + '">'
    body += '''<label for="username">Username</label><input id="username" name="username" required maxlength="80" autocomplete="username" autofocus>
<label for="password">Password</label><input type="password" id="password" name="password" required maxlength="1024" autocomplete="current-password">
<button type="submit">Sign in securely</button><small>Sessions expire after 30 minutes without activity or 8 hours after sign-in.</small></form></div>'''
    response = _page("Sign in", body)
    response.status_code = status
    response.set_cookie(LOGIN_CSRF_COOKIE, token, max_age=1800, httponly=True,
                        secure=_secure(request), samesite="strict", path="/")
    return response


async def _form(request: Request) -> dict[str, str]:
    return {key: str(value) for key, value in (await request.form()).items()}


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if getattr(request.state, "user", None):
            return RedirectResponse(_next(request.query_params.get("next", "/dashboard")), 303)
        return _login_page(request)

    @router.post("/login")
    async def login(request: Request):
        form = await _form(request)
        store = get_security(request)
        try:
            result = await run_in_threadpool(store.login, form.get("username", ""),
                                            form.get("password", ""), client_ip=_client_ip(request))
        except RateLimited as exc:
            response = _login_page(request, error=str(exc), status=429)
            response.headers["Retry-After"] = str(exc.retry_after)
            return response
        except AuthenticationError as exc:
            return _login_page(request, error=str(exc), status=401)
        # A reauthentication never leaves the previous cookie session alive.
        await run_in_threadpool(store.logout, request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse(_next(form.get("next", "/dashboard")), 303)
        response.set_cookie(SESSION_COOKIE, result.token, max_age=SESSION_SECONDS,
                            httponly=True, secure=_secure(request), samesite="strict", path="/")
        response.delete_cookie(LOGIN_CSRF_COOKIE, path="/", secure=_secure(request),
                               httponly=True, samesite="strict")
        return response

    @router.post("/logout")
    def logout(request: Request):
        get_security(request).logout(request.cookies.get(SESSION_COOKIE), client_ip=_client_ip(request))
        response = RedirectResponse("/login", 303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=_secure(request),
                               httponly=True, samesite="strict")
        return response

    @router.get("/v1/session")
    def session_info(request: Request):
        return {"user": current_user(request).public(), "csrf_token": request.state.csrf_token}

    @router.get("/account/password", response_class=HTMLResponse)
    def password_page(request: Request):
        user = current_user(request)
        body = '<div class="narrow"><h1>Change password</h1><p>Your current sessions will be signed out after this change.</p><form class="card" method="post">' + _hidden(request.state.csrf_token)
        body += '''<label for="current_password">Current password</label><input id="current_password" name="current_password" type="password" required autocomplete="current-password">
<label for="new_password">New password</label><input id="new_password" name="new_password" type="password" required minlength="12" maxlength="1024" autocomplete="new-password"><small>Use at least 12 characters.</small><button>Update password</button></form></div>'''
        return _page("Change password", body, user)

    @router.post("/account/password")
    async def password_update(request: Request):
        user, form = current_user(request), await _form(request)
        try:
            await run_in_threadpool(get_security(request).change_password, user.id,
                                    form.get("new_password", ""), actor_id=user.id,
                                    current_password=form.get("current_password", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response = RedirectResponse("/login", 303)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=_secure(request), httponly=True,
                               samesite="strict")
        return response

    @router.get("/admin/users", response_class=HTMLResponse)
    def users_page(request: Request):
        user = require_permission("users")(request)
        csrf = _hidden(request.state.csrf_token)
        body = '<p class="eyebrow">Administration</p><h1>People & access</h1><p class="muted">Inspectors conduct and review inspections. Supervisors also approve findings. Administrators manage accounts, rules and the audit log.</p>'
        if request.query_params.get("saved"):
            body += '<p class="success" role="status">Account change saved.</p>'
        body += '<div class="card table-wrap" tabindex="0"><table><thead><tr><th>Account</th><th>Access</th><th>Change access</th><th>Reset password</th></tr></thead><tbody>'
        for item in get_security(request).list_users():
            options = ''.join('<option value="' + role + '"' + (' selected' if item.role == role else '') + '>' + role.title() + '</option>' for role in ("inspector", "supervisor", "admin"))
            body += '<tr><td><strong>' + escape(item.display_name) + '</strong><small>' + escape(item.username) + '</small></td><td>' + escape(item.role.title()) + '<small>' + ('Active' if item.active else 'Deactivated') + '</small></td><td>'
            body += '<form method="post" action="/admin/users/' + item.id + '">' + csrf
            body += '<label class="muted" for="name-' + item.id + '">Display name</label><input id="name-' + item.id + '" name="display_name" value="' + escape(item.display_name, quote=True) + '" maxlength="120" required>'
            body += '<label for="role-' + item.id + '">Role</label><select id="role-' + item.id + '" name="role">' + options + '</select><label><input type="checkbox" name="active" value="1"' + (' checked' if item.active else '') + '> Active</label><button>Save access</button></form></td><td>'
            if item.id == user.id:
                body += '<a href="/account/password">Change your password</a>'
            else:
                body += '<form method="post" action="/admin/users/' + item.id + '/password">' + csrf + '<label for="reset-' + item.id + '">New password</label><input id="reset-' + item.id + '" type="password" name="new_password" required minlength="12" maxlength="1024" autocomplete="new-password"><button>Reset & sign out sessions</button></form>'
            body += '</td></tr>'
        body += '</tbody></table></div><div class="card"><h2>Create account</h2><form method="post" action="/admin/users">' + csrf
        body += '''<div class="grid"><div><label for="new-username">Username</label><input id="new-username" name="username" required minlength="3" maxlength="80" autocomplete="off"></div><div><label for="new-name">Display name</label><input id="new-name" name="display_name" maxlength="120"></div><div><label for="new-role">Role</label><select id="new-role" name="role"><option value="inspector">Inspector</option><option value="supervisor">Supervisor</option><option value="admin">Administrator</option></select></div><div><label for="new-password">Initial password</label><input id="new-password" type="password" name="password" required minlength="12" maxlength="1024" autocomplete="new-password"></div></div><button>Create account</button></form></div>'''
        return _page("People & access", body, user)

    @router.get("/v1/admin/users")
    def users_api(request: Request):
        require_permission("users")(request)
        return {"users": [user.public() for user in get_security(request).list_users()]}

    @router.post("/admin/users")
    async def create_user(request: Request):
        actor, form = require_permission("users")(request), await _form(request)
        try:
            await run_in_threadpool(get_security(request).create_user,
                                    form.get("username", ""), form.get("password", ""),
                                    display_name=form.get("display_name", ""),
                                    role=form.get("role", "inspector"), actor_id=actor.id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/admin/users?saved=1", 303)

    @router.post("/admin/users/{user_id}")
    async def update_user(request: Request, user_id: str):
        actor, form = require_permission("users")(request), await _form(request)
        try:
            await run_in_threadpool(get_security(request).update_user, user_id,
                                    actor_id=actor.id, role=form.get("role", ""),
                                    active=form.get("active") == "1",
                                    display_name=form.get("display_name", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/admin/users?saved=1", 303)

    @router.post("/admin/users/{user_id}/password")
    async def reset_password(request: Request, user_id: str):
        actor, form = require_permission("users")(request), await _form(request)
        try:
            await run_in_threadpool(get_security(request).change_password, user_id,
                                    form.get("new_password", ""), actor_id=actor.id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/admin/users?saved=1", 303)

    @router.get("/admin/audit", response_class=HTMLResponse)
    def audit_page(request: Request, page: int = 1, entity_id: str = ""):
        user = require_permission("audit")(request)
        page = max(1, page)
        events = get_security(request).audit_events(limit=50, offset=(page - 1) * 50,
                                                   entity_id=entity_id)
        body = '<p class="eyebrow">Accountability</p><h1>Audit log</h1><p class="muted">Append-only application events. Times are shown in UTC. Access to the database and its backups must be restricted at deployment.</p>'
        body += '<form method="get"><label for="entity">Inspection or entity ID</label><input id="entity" name="entity_id" value="' + escape(entity_id, quote=True) + '"><button>Filter events</button></form>'
        body += '<div class="card table-wrap" tabindex="0"><table><thead><tr><th>When (UTC)</th><th>Who</th><th>What</th><th>Entity</th><th>Before / after</th></tr></thead><tbody>'
        for event in events:
            when = datetime.fromtimestamp(event["occurred_at"], UTC).strftime("%Y-%m-%d %H:%M:%S")
            body += '<tr><td>' + when + '</td><td>' + escape(event["actor_username"]) + '</td><td>' + escape(event["action"]) + '</td><td>' + escape(event["entity_type"]) + '<small>' + escape(event["entity_id"] or "") + '</small></td><td><details><summary>View change</summary><strong>Before</strong><pre>' + escape(str(event["before"])) + '</pre><strong>After</strong><pre>' + escape(str(event["after"])) + '</pre></details></td></tr>'
        if not events:
            body += '<tr><td colspan="5">No events match this filter.</td></tr>'
        body += '</tbody></table></div><nav aria-label="Audit log pages">'
        query = '&entity_id=' + quote(entity_id, safe="")
        if page > 1:
            body += '<a href="?page=' + str(page - 1) + query + '">Previous page</a> '
        if len(events) == 50:
            body += '<a href="?page=' + str(page + 1) + query + '">Next page</a>'
        return _page("Audit log", body + '</nav>', user)

    @router.get("/v1/admin/audit")
    def audit_api(request: Request, page: int = 1, entity_id: str = ""):
        require_permission("audit")(request)
        return {"events": get_security(request).audit_events(limit=100, offset=(max(page, 1) - 1) * 100,
                                                            entity_id=entity_id)}

    return router


def install_security(app: FastAPI, database: str | Path) -> SecurityStore:
    """Protect all current and future routes. Replace app.state.security in tests."""
    store = SecurityStore(database)
    app.state.security = store
    app.include_router(build_router())
    app.add_middleware(SecurityMiddleware)
    return store
