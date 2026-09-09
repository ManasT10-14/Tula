# Authentication, roles and accountability

TATVA protects application pages, evidence files, exports and APIs with persisted
accounts. The login page, static assets and basic health endpoint are public.
Interactive API documentation also requires sign-in. No account, demo password,
anonymous-access switch or hardcoded session is shipped.

## Set up the first administrator

Run this from the repository using the same database as the web application:

```powershell
python -m tula.security bootstrap --database .\data\tula.db --username your.admin --display-name "Deployment Administrator"
```

Enter the password twice at the hidden prompts. Use at least 12 characters.
If `TULA_DATA_DIR` points to a different runtime directory, use its
`data/tula.db` file in the command. The command refuses to bootstrap again once
any account exists. Controlled provisioning may use `--password-stdin` to read
one password line from standard input; do not put passwords into command-line
arguments, source control, screenshots or shared scripts.

Open `/login`. Once signed in, administrators can open `/admin/users` to create
accounts, change roles, deactivate accounts and reset another user's password.
All users can change their own password at `/account/password`, supplying their
current password. Changing a password, role or activation status revokes existing
sessions. An administrator cannot demote or deactivate their own account; another
administrator must make that change. At least one active administrator is retained.

## Role boundaries

| Role | Capabilities |
| --- | --- |
| Inspector | Create inspections, review evidence, correct and review findings, history, reports and analytics |
| Supervisor | Inspector capabilities plus approval |
| Administrator | Supervisor capabilities plus users, rules, configuration and audit logs |

Middleware checks authentication for all current and future routes by default.
Administration routes are blocked for other roles even when called directly.
Inspection services must additionally enforce their ownership and independent
approval rules. A role permission alone does not prove that a particular finding
can be approved by that user.

## Sessions and passwords

Passwords use Python's `hashlib.scrypt` with a fresh 128-bit salt, N=32768, r=8,
p=3 and a 256-bit output. Passwords are never stored in readable form. Comparisons
use constant-time digest comparison. Password hashes are omitted from public user
objects and administration responses.

The session cookie contains a random 256-bit opaque token. SQLite stores only its
SHA-256 digest. Sessions expire after eight hours or thirty minutes without
activity; expiry is checked on every authenticated request. Logging out revokes
the server-side session, so replaying a copied old cookie cannot restore it.

Cookies are host-only, `HttpOnly`, `SameSite=Strict` and `Secure`. For a local
demonstration, HTTP is permitted only when both the URL host and the connecting
peer are loopback. This narrow local exception omits `Secure` because browsers
cannot consistently use a secure cookie on plain HTTP. Other HTTP requests to
protected pages receive HTTP 426. Tests use `https://testserver`.

Sign-in attempts are throttled persistently in SQLite across process restarts:
five unsuccessful attempts per account in fifteen minutes, plus forty attempts
per source IP in the same window. Parallel attempts reserve their bucket before
password verification. Responses do not disclose whether an account exists or is
deactivated. A valid login clears the account bucket, while the source-IP bucket
continues to account for requests. Unknown accounts still perform password-hash
verification to reduce identity disclosure through timing.

## CSRF and browser behavior

Every mutation requires a security token. Before login, a short-lived login form
token must match its host-only cookie. Authenticated tokens bind to the server-side
session. Requests from an explicitly different `Origin` or with
`Sec-Fetch-Site: cross-site` are rejected.

URL-encoded forms include a `csrf_token` hidden field. HTMX, fetch, JSON and
multipart uploads send `X-CSRF-Token`; `/v1/session` returns the current token to
an authenticated client. An uploaded multipart body is never buffered just to
extract a token. The application must pass the header before uploading files.

Responses disable sensitive-data caching and set content-type sniffing, framing,
referrer and permissions protections. The content-security policy permits the
application's existing inline scripts/styles, but blocks foreign script sources,
plugins, framing and foreign form destinations. Inline script permission means
the policy does not replace output escaping; rendered account and audit values
are escaped. HTTPS responses set HSTS.

## Audit log and migrations

The security schema adds separate user, role, session, attempt and audit tables to
the existing SQLite database. Initialization is idempotent and does not replace
inspection records. Role values, active states and user/session relationships
have database constraints. Writes use transactions, bound SQL values, foreign
keys and SQLite's busy timeout.

`/admin/audit` shows who acted, the time, the action, the entity and before/after
values. Account creation/changes, password changes, successful/failed logins and
logout are recorded by the security service. Inspection services add their own
creation, processing, correction, verification and report events through the
same API. Password and session fields are redacted from structured audit payloads.

Application-level audit entries cannot be updated or deleted: SQLite triggers
reject those operations. This is not an external tamper-proof ledger. A person
with unrestricted database or filesystem access could replace a database or
remove its triggers. Protect the runtime directory, restrict database backups,
and use an external retained audit destination if deployment policy requires one.

## Integration and deployment

`install_security(app, database_path)` installs the middleware and routes, and
stores `SecurityStore` at `app.state.security`. Request state exposes `user` and
`csrf_token`. Use `require_permission(...)` or `require_roles(...)` at backend
boundaries. The store also verifies administrator identity before changing users.
Tests may replace `app.state.security` with an isolated real store; there is no
production authentication bypass.

For non-local deployments, terminate HTTPS using a trusted reverse proxy. Configure
Uvicorn to trust forwarded protocol/client headers only from that proxy's IPs;
never allow arbitrary internet peers to claim HTTPS or a loopback address. The
application does not independently trust `X-Forwarded-For`. Keep the database,
original images, generated reports and backups outside a public static directory.
Account provisioning and password reset use an authorized administrator; this
installation does not provide email password recovery, MFA or SSO.

Run the security regression suite with:

```powershell
python -m pytest tests/test_security.py -q
```

The suite exercises persisted password/session handling, old-database preservation,
expiry, logout replay, throttling, CSRF, transport policy, output escaping,
role boundaries, account forms and immutable audit records.
