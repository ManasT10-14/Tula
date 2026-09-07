"""Persistent local identities, sessions and append-only application audit events.

This module deliberately has no FastAPI dependency. All values interpolated into
SQL are controlled column names; user values use bound parameters.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROLES = frozenset({"inspector", "supervisor", "admin"})
PERMISSIONS = {
    "inspector": frozenset({"inspect", "review", "report", "history", "analytics"}),
    "supervisor": frozenset({"inspect", "review", "approve", "report", "history", "analytics"}),
    "admin": frozenset({"inspect", "review", "approve", "report", "history", "analytics",
                        "users", "rules", "configuration", "audit"}),
}
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60
THROTTLE_SECONDS = 15 * 60
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 32768, 8, 3
_SENSITIVE = {"password", "password_hash", "token", "session_token", "csrf_token", "secret"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS security_migration (
    version INTEGER PRIMARY KEY, applied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS security_role (
    name TEXT PRIMARY KEY CHECK(name IN ('inspector','supervisor','admin'))
);
INSERT OR IGNORE INTO security_role(name) VALUES ('inspector'), ('supervisor'), ('admin');
CREATE TABLE IF NOT EXISTS security_user (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL REFERENCES security_role(name),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS security_session (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES security_user(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_session_user ON security_session(user_id);
CREATE INDEX IF NOT EXISTS idx_security_session_expiry ON security_session(expires_at);
CREATE TABLE IF NOT EXISTS security_login_attempt (
    bucket TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    window_start REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS security_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at REAL NOT NULL,
    actor_id TEXT,
    actor_username TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    client_ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_security_audit_time ON security_audit(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_security_audit_entity ON security_audit(entity_type, entity_id);
CREATE TRIGGER IF NOT EXISTS security_audit_no_update
BEFORE UPDATE ON security_audit BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS security_audit_no_delete
BEFORE DELETE ON security_audit BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
"""


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    role: str
    active: bool

    def can(self, permission: str) -> bool:
        return self.active and permission in PERMISSIONS.get(self.role, ())

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str
    expires_at: float


@dataclass(frozen=True)
class Login:
    token: str
    session: Session


class AuthenticationError(ValueError):
    """The submitted credentials are not usable (intentionally non-specific)."""


class RateLimited(AuthenticationError):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, retry_after)
        super().__init__("Too many sign-in attempts. Please wait before trying again.")


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(password) < 12 or len(raw) > 1024:
        raise ValueError("Use a password of at least 12 characters and at most 1024 UTF-8 bytes.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(raw, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (
            _SCRYPT_N, _SCRYPT_R, _SCRYPT_P
        ) or len(password.encode("utf-8")) > 1024:
            return False
        salt_bytes, digest_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(digest_bytes) != 32:
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt_bytes,
                                n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024,
                                dklen=32)
        return hmac.compare_digest(actual, digest_bytes)
    except (ValueError, TypeError, OverflowError):
        return False


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[redacted]" if str(k).lower() in _SENSITIVE else _redact(v)
                for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_redact(v) for v in value]
    return value


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"], bool(row["active"]))


class SecurityStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO security_migration VALUES (1, ?)", (clock(),))
        # Unknown identities still incur the password verification work.
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _audit(self, conn: sqlite3.Connection, *, actor_id: str | None, action: str,
               entity_type: str, entity_id: str | None = None, before: Any = None,
               after: Any = None, client_ip: str | None = None) -> None:
        actor = conn.execute("SELECT username FROM security_user WHERE id=?", (actor_id,)).fetchone()
        conn.execute(
            "INSERT INTO security_audit (occurred_at, actor_id, actor_username, action,"
            " entity_type, entity_id, before_json, after_json, client_ip) VALUES (?,?,?,?,?,?,?,?,?)",
            (self.clock(), actor_id, actor[0] if actor else "system", action, entity_type, entity_id,
             json.dumps(_redact(before), ensure_ascii=False, default=str) if before is not None else None,
             json.dumps(_redact(after), ensure_ascii=False, default=str) if after is not None else None,
             client_ip),
        )

    def audit(self, *, actor_id: str | None, action: str, entity_type: str,
              entity_id: str | None = None, before: Any = None, after: Any = None,
              client_ip: str | None = None) -> None:
        with self._connect(write=True) as conn:
            self._audit(conn, actor_id=actor_id, action=action, entity_type=entity_type,
                        entity_id=entity_id, before=before, after=after, client_ip=client_ip)

    def audit_events(self, *, limit: int = 100, offset: int = 0,
                     entity_type: str = "", entity_id: str = "") -> list[dict[str, Any]]:
        conditions, params = [], []
        for column, value in (("entity_type", entity_type), ("entity_id", entity_id)):
            if value:
                conditions.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM security_audit" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
                                (*params, max(1, min(limit, 500)), max(0, offset))).fetchall()
        events = [dict(row) for row in rows]
        for event in events:
            event["before"] = json.loads(event.pop("before_json") or "null")
            event["after"] = json.loads(event.pop("after_json") or "null")
        return events

    def has_users(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM security_user LIMIT 1").fetchone() is not None

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            return [_user(row) for row in conn.execute(
                "SELECT * FROM security_user ORDER BY active DESC, username"
            ).fetchall()]

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM security_user WHERE id=?", (user_id,)).fetchone()
            return _user(row) if row else None

    def create_user(self, username: str, password: str, *, display_name: str = "",
                    role: str = "inspector", actor_id: str | None = None,
                    bootstrap: bool = False) -> User:
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._@+-]{2,79}", username):
            raise ValueError("Username must be 3–80 letters, numbers or . _ @ + - characters.")
        if role not in ROLES:
            raise ValueError("Choose inspector, supervisor or admin.")
        display_name = display_name.strip() or username
        if len(display_name) > 120:
            raise ValueError("Display name must be 120 characters or fewer.")
        encoded = hash_password(password)
        user = User(uuid.uuid4().hex, username, display_name, role, True)
        with self._connect(write=True) as conn:
            if not bootstrap:
                self._require_admin(conn, actor_id)
            if bootstrap and conn.execute("SELECT 1 FROM security_user LIMIT 1").fetchone():
                raise ValueError("An account already exists. Sign in as an administrator to manage users.")
            if bootstrap and role != "admin":
                raise ValueError("The first account must be an administrator.")
            try:
                conn.execute("INSERT INTO security_user VALUES (?,?,?,?,?,1,?,?)",
                             (user.id, username, display_name, encoded, role,
                              self.clock(), self.clock()))
            except sqlite3.IntegrityError as exc:
                raise ValueError("That username is already in use.") from exc
            self._audit(conn, actor_id=actor_id, action="user.created", entity_type="user",
                        entity_id=user.id, after=user.public())
        return user

    def update_user(self, user_id: str, *, actor_id: str, role: str,
                    active: bool, display_name: str | None = None) -> User:
        if role not in ROLES:
            raise ValueError("Choose inspector, supervisor or admin.")
        with self._connect(write=True) as conn:
            self._require_admin(conn, actor_id)
            row = conn.execute("SELECT * FROM security_user WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("No such user.")
            before = _user(row)
            if user_id == actor_id and (not active or role != before.role):
                raise ValueError("Ask another administrator to change your own role or deactivate you.")
            if before.active and before.role == "admin" and (role != "admin" or not active):
                count = conn.execute(
                    "SELECT COUNT(*) FROM security_user WHERE active=1 AND role='admin'"
                ).fetchone()[0]
                if count <= 1:
                    raise ValueError("Keep at least one active administrator.")
            name = before.display_name if display_name is None else display_name.strip()
            if not name or len(name) > 120:
                raise ValueError("Display name must contain 1–120 characters.")
            conn.execute("UPDATE security_user SET role=?,active=?,display_name=?,updated_at=? WHERE id=?",
                         (role, int(active), name, self.clock(), user_id))
            if role != before.role or active != before.active:
                conn.execute("DELETE FROM security_session WHERE user_id=?", (user_id,))
            after = User(user_id, before.username, name, role, active)
            self._audit(conn, actor_id=actor_id, action="user.updated", entity_type="user",
                        entity_id=user_id, before=before.public(), after=after.public())
        return after

    def change_password(self, user_id: str, new_password: str, *, actor_id: str,
                        current_password: str | None = None) -> None:
        encoded = hash_password(new_password)
        with self._connect(write=True) as conn:
            if user_id != actor_id:
                self._require_admin(conn, actor_id)
            row = conn.execute("SELECT * FROM security_user WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ValueError("No such user.")
            if user_id == actor_id and not verify_password(current_password or "", row["password_hash"]):
                raise ValueError("Your current password was not accepted.")
            conn.execute("UPDATE security_user SET password_hash=?,updated_at=? WHERE id=?",
                         (encoded, self.clock(), user_id))
            conn.execute("DELETE FROM security_session WHERE user_id=?", (user_id,))
            self._audit(conn, actor_id=actor_id, action="user.password_changed", entity_type="user",
                        entity_id=user_id, after={"sessions_revoked": True})

    @staticmethod
    def _require_admin(conn: sqlite3.Connection, actor_id: str | None) -> None:
        actor = conn.execute("SELECT role,active FROM security_user WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor["role"] != "admin" or not actor["active"]:
            raise ValueError("An active administrator must authorize account management.")

    @staticmethod
    def _buckets(username: str, client_ip: str) -> tuple[tuple[str, int], ...]:
        return (("account:" + _digest(username.lower().strip()), 5),
                ("ip:" + _digest(client_ip), 40))

    def login(self, username: str, password: str, *, client_ip: str = "unknown") -> Login:
        username = username.strip().lower()[:200]
        now = self.clock()
        buckets = self._buckets(username, client_ip)
        # Reserve an attempt atomically before the expensive password hash so
        # parallel requests cannot all race past the throttle check.
        with self._connect(write=True) as conn:
            conn.execute("DELETE FROM security_login_attempt WHERE window_start<=?",
                         (now - THROTTLE_SECONDS,))
            for bucket, maximum in buckets:
                attempt = conn.execute("SELECT * FROM security_login_attempt WHERE bucket=?",
                                       (bucket,)).fetchone()
                if attempt and attempt["failures"] >= maximum:
                    raise RateLimited(int(attempt["window_start"] + THROTTLE_SECONDS - now) + 1)
            for bucket, _ in buckets:
                conn.execute("INSERT INTO security_login_attempt VALUES (?,1,?) ON CONFLICT(bucket)"
                             " DO UPDATE SET failures=failures+1", (bucket, now))
            row = conn.execute("SELECT * FROM security_user WHERE username=?", (username,)).fetchone()
        valid = verify_password(password, row["password_hash"] if row else self._dummy_hash)
        with self._connect(write=True) as conn:
            # Recheck after hashing so simultaneous account changes cannot issue
            # a session for deactivated users or a superseded password.
            current = conn.execute("SELECT * FROM security_user WHERE username=?", (username,)).fetchone()
            if not valid or not current or not current["active"] or (
                row and current["password_hash"] != row["password_hash"]
            ):
                self._audit(conn, actor_id=current["id"] if current else None, action="login.failed",
                            entity_type="authentication", client_ip=client_ip)
                # Commit the failure event before raising outside the transaction.
                result = None
            else:
                user = _user(current)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                conn.execute("DELETE FROM security_session WHERE expires_at<=? OR last_seen<=?",
                             (now, now - IDLE_SECONDS))
                conn.execute("INSERT INTO security_session VALUES (?,?,?,?,?,?)",
                             (_digest(token), user.id, csrf, now, now + SESSION_SECONDS, now))
                # Valid credentials release the account bucket; keep IP request
                # accounting so one known account cannot clear a shared-IP attack.
                conn.execute("DELETE FROM security_login_attempt WHERE bucket=?", (buckets[0][0],))
                self._audit(conn, actor_id=user.id, action="login.succeeded", entity_type="authentication",
                            client_ip=client_ip)
                result = Login(token, Session(user, csrf, now + SESSION_SECONDS))
        if result is None:
            raise AuthenticationError("The username or password was not accepted.")
        return result

    def session(self, token: str | None) -> Session | None:
        if not token or not 20 <= len(token) <= 100:
            return None
        now, token_hash = self.clock(), _digest(token)
        with self._connect(write=True) as conn:
            row = conn.execute("SELECT u.*,s.csrf_token,s.expires_at,s.last_seen FROM security_session s"
                               " JOIN security_user u ON u.id=s.user_id WHERE s.token_hash=?",
                               (token_hash,)).fetchone()
            if not row:
                return None
            if not row["active"] or row["expires_at"] <= now or row["last_seen"] <= now - IDLE_SECONDS:
                conn.execute("DELETE FROM security_session WHERE token_hash=?", (token_hash,))
                return None
            if row["last_seen"] < now - 60:
                conn.execute("UPDATE security_session SET last_seen=? WHERE token_hash=?", (now, token_hash))
            return Session(_user(row), row["csrf_token"], row["expires_at"])

    def logout(self, token: str | None, *, client_ip: str | None = None) -> None:
        if not token:
            return
        with self._connect(write=True) as conn:
            row = conn.execute("SELECT user_id FROM security_session WHERE token_hash=?",
                               (_digest(token),)).fetchone()
            conn.execute("DELETE FROM security_session WHERE token_hash=?", (_digest(token),))
            if row:
                self._audit(conn, actor_id=row[0], action="logout", entity_type="authentication",
                            client_ip=client_ip)
