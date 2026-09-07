"""Audited runtime rule selection; imports are data and never executable code."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from datetime import UTC, date, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from ..domain.enums import AssuranceTier, DeclarationClass, Panel, Script
from ..domain.models import Declaration, PackageFacts
from ..rules.engine import RulesEngine
from ..rules.legal import validate_legal_metadata, validate_legal_screen
from ..rules.spec import Messages, Rule, RuleCitation, RulePack
from ..security.web import require_roles

MAX_PACK_BYTES = 512 * 1024
ACTIVE_KEY = "active_rule_version"
_LOCK = threading.RLock()
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_FIELD = re.compile(r"\$(decl|pkg|meas|ctx)(?:\.[a-z][a-z0-9_]*){1,6}\Z")


def _number(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or abs(value) > 1e12 or not math.isfinite(value) or (positive and value <= 0)):
        raise ValueError("Rule numeric arguments must be finite, bounded numbers.")


def _field(value):
    if not isinstance(value, str) or not _FIELD.fullmatch(value) or "__" in value:
        raise ValueError("Rule fields must use public $decl, $pkg, $meas or $ctx paths.")
    root, name, *tail = value[1:].split(".")
    if root == "decl":
        DeclarationClass(name)
        if tail and tail[0] not in Declaration.model_fields:
            raise ValueError("Unknown declaration field in rule.")
    elif root == "pkg" and name not in {*PackageFacts.model_fields, "is_exempt"}:
        raise ValueError("Unknown package field in rule.")
    elif root == "ctx" and name not in {
            "tier", "lane", "packing_date", "captured_at", "panels", "assessment_date", "date_basis"}:
        raise ValueError("Unknown assessment context field in rule.")


def validate_expression(node, *, truth=True, depth=0, counter=None):
    """Whitelist evaluator syntax, arity and literal types before importing.

    No Python, arbitrary attributes or unrestricted regular expressions enter
    the interpreter. Limits also prevent deep/large expression resource abuse.
    """
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if depth > 20 or counter[0] > 3000:
        raise ValueError("Rule expressions exceed the depth or node limit.")
    if node is None or isinstance(node, bool):
        return
    if isinstance(node, str):
        if len(node) > 1000:
            raise ValueError("Rule literal text is too long.")
        if truth or node.startswith("$"):
            _field(node)
        return
    if isinstance(node, (int, float)) and not truth:
        _number(node)
        return
    if not isinstance(node, dict) or len(node) != 1:
        raise ValueError("Each expression must contain exactly one known operator.")
    op, arg = next(iter(node.items()))

    def child(value, *, boolean=True):
        validate_expression(value, truth=boolean, depth=depth + 1, counter=counter)

    def args(low, high=None):
        if not isinstance(arg, list) or not low <= len(arg) <= (high if high is not None else low):
            raise ValueError(f"Operator {op} has an invalid number of arguments.")

    if op in {"all", "any"}:
        args(1, 100)
        for value in arg:
            child(value)
    elif op == "not":
        child(arg)
    elif op in {"present", "absent", "truthy", "value_of", "len"}:
        _field(arg)
        if truth and op in {"value_of", "len"}:
            raise ValueError(f"{op} must be used inside a comparison, not as a truth assertion.")
    elif op in {"eq", "ne", "gt", "gte", "lt", "lte", "gte_measured"}:
        args(2)
        if op == "gte_measured":
            _field(arg[0])
        for value in arg:
            child(value, boolean=False)
    elif op in {"divide", "approx_eq", "rounded_to"}:
        args(1 if op == "rounded_to" else 2, 2 if op == "rounded_to" else 3)
        for value in arg[:2 if op != "rounded_to" else 1]:
            child(value, boolean=False)
        if op == "divide":
            if len(arg) == 3:
                child(arg[2], boolean=False)
            if truth:
                raise ValueError("divide must be used inside a comparison.")
        elif op == "rounded_to" and len(arg) == 2:
            _number(arg[1], positive=True)
        elif op == "approx_eq" and len(arg) == 3:
            _number(arg[2])
            if not 0 <= arg[2] <= 1:
                raise ValueError("Relative tolerance must be between 0 and 1.")
    elif op == "in":
        args(2)
        child(arg[0], boolean=False)
        if not isinstance(arg[1], list) or not 1 <= len(arg[1]) <= 100:
            raise ValueError("Membership requires a bounded list of literal values.")
        for value in arg[1]:
            if isinstance(value, (dict, list)) or (isinstance(value, str) and value.startswith("$")):
                raise ValueError("Membership choices must be literals.")
            child(value, boolean=False)
    elif op == "contains_phrase":
        args(2, 3)
        _field(arg[0])
        phrases = arg[1] if isinstance(arg[1], list) else [arg[1]]
        if not 1 <= len(phrases) <= 20 or not all(isinstance(v, str) and 1 <= len(v) <= 250 for v in phrases):
            raise ValueError("Phrase matching requires one to twenty short literal phrases.")
        if len(arg) == 3:
            _number(arg[2])
            if not 0 <= arg[2] <= 1:
                raise ValueError("Phrase similarity must be between 0 and 1.")
    elif op == "matches":
        args(2)
        _field(arg[0])
        pattern = arg[1]
        if (not isinstance(pattern, str) or len(pattern) > 200 or any(c in pattern for c in "()*+|?")
                or re.search(r"\\[1-9]", pattern)):
            raise ValueError("Regex checks support only simple literals, anchors, character classes and fixed repetitions.")
        for repetition in re.findall(r"\{([^}]+)\}", pattern):
            if not re.fullmatch(r"\d{1,2}", repetition):
                raise ValueError("Regex repetition must be a fixed count at most 99.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError("Invalid regex in rule.") from exc
    elif op == "same_units_arithmetic":
        args(3)
        _field(arg[0])
        _field(arg[1])
        child(arg[2])
    elif op == "unit_price_matches":
        args(4)
        for value in arg:
            _field(value)
    elif op == "tier_at_least":
        AssuranceTier(arg)
    elif op == "legal_screen":
        validate_legal_screen(arg)
    elif op == "script_coverage":
        args(2)
        DeclarationClass(arg[0])
        if not isinstance(arg[1], list) or not arg[1]:
            raise ValueError("Script coverage requires a nonempty list of scripts.")
        for value in arg[1]:
            Script(value)
    elif op == "panel_is":
        args(2)
        DeclarationClass(arg[0])
        panels = arg[1] if isinstance(arg[1], list) else [arg[1]]
        if not 1 <= len(panels) <= len(Panel):
            raise ValueError("Choose valid declaration panels.")
        for value in panels:
            Panel(value)
    elif op == "same_panel":
        args(2, len(DeclarationClass))
        for value in arg:
            DeclarationClass(value)
    elif op == "exempt_under":
        exemptions = arg if isinstance(arg, list) else [arg]
        if not 1 <= len(exemptions) <= 30 or not all(isinstance(v, str) and 1 <= len(v) <= 100 for v in exemptions):
            raise ValueError("Exemption references must be short literal strings.")
    elif op == "table_lookup":
        if truth:
            raise ValueError("A threshold table must be used inside a comparison.")
        _validate_table(arg)
    else:
        raise ValueError(f"Unknown or unsupported rule operator: {op}.")


def _validate_table(arg):
    if not isinstance(arg, dict) or set(arg) - {"key", "rows", "select", "boundary_policy"}:
        raise ValueError("Invalid threshold-table fields.")
    _field(arg.get("key"))
    if arg.get("boundary_policy", "point") not in {"point", "favour_subject"}:
        raise ValueError("Unknown table boundary policy.")
    rows = arg.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("A threshold table requires one to one hundred rows.")
    select = arg.get("select", "value")
    if isinstance(select, dict):
        if set(select) != {"if", "then", "else"}:
            raise ValueError("Conditional table columns require if, then and else.")
        _field(select["if"])
        columns = [select["then"], select["else"]]
    else:
        columns = [select]
    if not all(isinstance(c, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", c) and c != "max" for c in columns):
        raise ValueError("Threshold column names must be plain identifiers.")
    previous = -math.inf
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"max", *columns}:
            raise ValueError("Each threshold row must supply its maximum and selected numeric columns.")
        cap = row["max"]
        if cap is None:
            if index != len(rows) - 1:
                raise ValueError("An open-ended threshold band must be last.")
        else:
            _number(cap)
            if cap <= previous:
                raise ValueError("Threshold maxima must increase strictly.")
            previous = cap
        for column in columns:
            _number(row[column])


def _known_fields(raw, model):
    if not isinstance(raw, dict):
        raise TypeError(f"{model.__name__} must be a JSON object.")
    allowed = set(model.model_fields)
    allowed.update(f.alias for f in model.model_fields.values() if f.alias)
    if set(raw) - allowed:
        raise ValueError(f"Unknown {model.__name__} fields: {', '.join(sorted(set(raw) - allowed))}.")
    for name, info in model.model_fields.items():
        if info.alias and info.alias != name and name in raw and info.alias in raw:
            raise ValueError(f"Provide {info.alias} only once.")


def parse_rule_pack(text: str) -> RulePack:
    if len(text.encode("utf-8")) > MAX_PACK_BYTES:
        raise ValueError("Rule-pack JSON must be 512 KB or smaller.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}.")
            result[key] = value
        return result

    try:
        raw = json.loads(text, object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON values are not permitted.")))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Supply a valid, bounded JSON rule pack.") from exc
    _known_fields(raw, RulePack)
    if not isinstance(raw.get("rules"), list) or not 1 <= len(raw["rules"]) <= 100:
        raise ValueError("A draft must contain between 1 and 100 rules.")
    for row in raw["rules"]:
        _known_fields(row, Rule)
        _known_fields(row.get("citation"), RuleCitation)
        for name in ("effective_from", "effective_to"):
            value = row.get(name)
            if value is None and name == "effective_to":
                continue
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError(f"{name} must be an ISO date in YYYY-MM-DD form.")
        if "messages" in row:
            _known_fields(row["messages"], Messages)
        if "assert" not in row and "assertion" not in row:
            raise ValueError("Each imported rule must state its assertion explicitly.")
    try:
        pack = RulePack.model_validate(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError(f"Rule-pack schema validation failed: {str(exc)[:1200]}") from exc
    if not _VERSION.fullmatch(pack.version):
        raise ValueError("Version identifiers use 1–80 letters, digits, dots, underscores or hyphens.")
    source = urlsplit(pack.source)
    if source.scheme not in {"http", "https"} or not source.netloc or source.username or source.password:
        raise ValueError("Provide an HTTP(S) legal source URL without embedded credentials.")
    if not pack.title.strip() or not pack.notes.strip():
        raise ValueError("Provide a title and legal review notes describing source and limitations.")
    if len({r.id for r in pack.rules}) != len(pack.rules):
        raise ValueError("Rule identifiers must be unique within a version.")
    counter = [0]
    for rule in pack.rules:
        if not rule.id.strip() or len(rule.id) > 120 or not rule.title.strip() or not rule.citation.clause.strip():
            raise ValueError("Each rule needs an identifier, title and legal clause.")
        if rule.effective_to and rule.effective_to < rule.effective_from:
            raise ValueError("Rule end dates must not precede their start dates.")
        validate_expression(rule.applies_when, counter=counter)
        validate_expression(rule.assertion, counter=counter)
    validate_legal_metadata(pack)
    return pack


def _configuration(repo):
    with repo._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_configuration ("
                     "key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL,actor_id TEXT,reason TEXT NOT NULL)")


def load_active_rules(repo, default_engine):
    """Read persisted selection on startup; broken selections fail visibly."""
    _configuration(repo)
    with repo._connect() as conn:
        row = conn.execute("SELECT value FROM app_configuration WHERE key=?", (ACTIVE_KEY,)).fetchone()
    if row is None:
        return default_engine
    raw = repo.archived_rules(row["value"])
    if raw is None:
        raise ValueError("The configured active rule version is missing from the archive.")
    return RulesEngine(parse_rule_pack(json.dumps(raw)))


def install_admin_rules(web):
    app = web.app
    _configuration(web.repo)
    admin = require_roles("admin")

    def versions():
        with web.repo._connect() as conn:
            rows = conn.execute("SELECT version,sha256,record,created_at,actor_id,note FROM rule_version ORDER BY created_at DESC").fetchall()
        output = []
        for row in rows:
            raw = json.loads(row["record"])
            output.append({**dict(row), "title": raw.get("title"), "source": raw.get("source"),
                           "notes": raw.get("notes"), "rule_count": len(raw.get("rules", [])),
                           "active": row["version"] == web.rules.pack.version})
        return output

    @app.get("/admin/rules")
    def page(request: Request):
        admin(request)
        return web.templates.TemplateResponse(request, "admin_rules.html", {
            "active": web.rules.pack, "versions": versions(), "nav": "rules",
            "rules_version": web.rules.pack.version})

    @app.get("/v1/admin/rules")
    def list_versions(request: Request):
        admin(request)
        return {"active_version": web.rules.pack.version,
                "versions": [{k: v for k, v in row.items() if k != "record"} for row in versions()]}

    @app.get("/v1/admin/rules/{version}")
    def download(request: Request, version: str):
        admin(request)
        if not _VERSION.fullmatch(version):
            raise HTTPException(404, "No such rule version.")
        with web.repo._connect() as conn:
            row = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (version,)).fetchone()
        if row is None:
            raise HTTPException(404, "No such rule version.")
        if hashlib.sha256(row["record"].encode()).hexdigest() != row["sha256"]:
            raise HTTPException(409, "The archived rule content failed its integrity check.")
        return Response(row["record"], media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="tula-rules-{version}.json"'})

    @app.post("/admin/rules/import")
    async def import_draft(request: Request, rule_json: str = Form(""), reason: str = Form(...),
                           rule_file: Annotated[UploadFile | None, File()] = None):
        actor = admin(request)
        try:
            if not 5 <= len(reason.strip()) <= 3000:
                raise ValueError("Explain the draft and source review in 5–3,000 characters.")
            if rule_file is not None and rule_file.filename:
                if rule_json.strip():
                    raise ValueError("Upload a JSON file or paste JSON; choose one source.")
                payload = await rule_file.read(MAX_PACK_BYTES + 1)
                if len(payload) > MAX_PACK_BYTES:
                    raise ValueError("Rule-pack JSON must be 512 KB or smaller.")
                rule_json = payload.decode("utf-8-sig")
            pack = parse_rule_pack(rule_json)
            with _LOCK, web.repo._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                web.repo.archive_rules(pack, actor_id=actor.id, note=reason.strip(), connection=conn)
                app.state.security._audit(conn, actor_id=actor.id, action="rules.draft_imported", entity_type="rule_version",
                                          entity_id=pack.version, after={"version": pack.version, "source": pack.source,
                                                                        "rules": len(pack.rules), "reason": reason.strip()})
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from None
        return RedirectResponse("/admin/rules#imported", status_code=303)

    @app.post("/admin/rules/activate")
    def activate(request: Request, version: str = Form(...), expected_active: str = Form(...),
                 reason: str = Form(...)):
        actor = admin(request)
        if not 5 <= len(reason.strip()) <= 3000:
            raise HTTPException(422, "Explain activation or rollback in 5–3,000 characters.")
        with _LOCK:
            raw = web.repo.archived_rules(version)
            if raw is None:
                raise HTTPException(404, "No such archived rule version.")
            try:
                pack = parse_rule_pack(json.dumps(raw))
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, f"This archived version cannot be activated: {exc}") from None
            with web.repo._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT value FROM app_configuration WHERE key=?", (ACTIVE_KEY,)).fetchone()
                before = row["value"] if row else web.rules.pack.version
                if expected_active != before:
                    raise HTTPException(409, "The active version changed. Reload and review the current selection.")
                conn.execute("INSERT INTO app_configuration (key,value,updated_at,actor_id,reason) VALUES (?,?,?,?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,"
                             "actor_id=excluded.actor_id,reason=excluded.reason",
                             (ACTIVE_KEY, pack.version, datetime.now(UTC).isoformat(), actor.id, reason.strip()))
                # Use the same connection so an audit failure rolls activation
                # back, rather than leaving an unaudited live rule change.
                app.state.security._audit(conn, actor_id=actor.id, action="rules.activated", entity_type="rule_version",
                                          entity_id=pack.version, before={"active_version": before},
                                          after={"active_version": pack.version, "reason": reason.strip()})
            web.rules = RulesEngine(pack)
            worker = getattr(app.state, "jobs", None)
            if worker is not None and worker.repo.path == web.repo.path:
                worker.rules = web.rules
        return RedirectResponse("/admin/rules#active-version", status_code=303)
