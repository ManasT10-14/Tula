"""Additive relational catalog over retained inspection and rule archives.

All writes use the caller's SQLite transaction. The source records remain
untouched; indexed revisions reject conflicting content. Missing historical
definitions are explicit nullable links, never fabricated rule definitions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from functools import wraps

from ..domain.models import EvidenceCoverage
from ..forensics.gtin import check as check_gtin

DDL = (
    """CREATE TABLE IF NOT EXISTS catalog_product (
        product_id TEXT PRIMARY KEY,
        identity_kind TEXT NOT NULL CHECK(identity_kind IN ('gtin','inspection')),
        gtin TEXT UNIQUE CHECK(gtin IS NULL OR (length(gtin)=14 AND gtin NOT GLOB '*[^0-9]*')),
        owner_scan_id TEXT REFERENCES inspection(scan_id),
        identity_basis TEXT NOT NULL,
        CHECK((identity_kind='gtin' AND gtin IS NOT NULL AND owner_scan_id IS NULL)
           OR (identity_kind='inspection' AND gtin IS NULL AND owner_scan_id IS NOT NULL)))""",
    """CREATE TABLE IF NOT EXISTS inspection_product_revision (
        scan_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
        product_id TEXT NOT NULL REFERENCES catalog_product(product_id),
        reported_gtin TEXT, brand TEXT, generic_name TEXT, manufacturer TEXT,
        net_quantity REAL, net_unit TEXT, mrp REAL, unit_price REAL,
        category TEXT NOT NULL, category_confirmed INTEGER NOT NULL CHECK(category_confirmed IN (0,1)),
        captured_at TEXT NOT NULL, review_status TEXT NOT NULL,
        source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
        PRIMARY KEY(scan_id,revision),
        FOREIGN KEY(scan_id,revision) REFERENCES inspection_revision(scan_id,revision))""",
    """CREATE TABLE IF NOT EXISTS catalog_rule_definition (
        version TEXT NOT NULL REFERENCES rule_version(version), rule_id TEXT NOT NULL,
        title TEXT NOT NULL, act TEXT NOT NULL, rules_name TEXT NOT NULL,
        clause TEXT NOT NULL, citation_text TEXT NOT NULL,
        effective_from TEXT, effective_to TEXT, declaration_kind TEXT, severity TEXT,
        minimum_tier TEXT, definition_json TEXT NOT NULL,
        definition_sha256 TEXT NOT NULL CHECK(length(definition_sha256)=64),
        PRIMARY KEY(version,rule_id))""",
    """CREATE TABLE IF NOT EXISTS catalog_rule_source (
        version TEXT NOT NULL, rule_id TEXT NOT NULL, source_index INTEGER NOT NULL CHECK(source_index>=0),
        url TEXT NOT NULL, notification TEXT, publication_date TEXT, clause TEXT, review_status TEXT,
        PRIMARY KEY(version,rule_id,source_index),
        FOREIGN KEY(version,rule_id) REFERENCES catalog_rule_definition(version,rule_id))""",
    """CREATE TABLE IF NOT EXISTS compliance_finding (
        scan_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0), finding_id TEXT NOT NULL,
        rule_id TEXT NOT NULL, requested_rules_version TEXT NOT NULL,
        definition_version TEXT, definition_rule_id TEXT,
        rule_link_status TEXT NOT NULL CHECK(rule_link_status IN ('resolved','missing_version','missing_rule')),
        declaration_kind TEXT, clause TEXT NOT NULL, citation_text TEXT NOT NULL,
        machine_verdict TEXT NOT NULL CHECK(machine_verdict IN ('PASS','VIOLATION','ADVISORY','INCONCLUSIVE','UNVERIFIED','EXEMPT','NOT_APPLICABLE')),
        severity TEXT NOT NULL CHECK(severity IN ('critical','major','minor')),
        message TEXT NOT NULL, detail TEXT NOT NULL, tier TEXT,
        measured_value REAL, measured_uncertainty REAL, measured_unit TEXT, threshold REAL, threshold_basis TEXT,
        reviewed_verdict TEXT CHECK(reviewed_verdict IS NULL OR reviewed_verdict IN ('PASS','VIOLATION','ADVISORY','INCONCLUSIVE','UNVERIFIED','EXEMPT','NOT_APPLICABLE')),
        review_reason TEXT, reviewer_id TEXT, reviewed_at TEXT,
        PRIMARY KEY(scan_id,revision,finding_id),
        FOREIGN KEY(scan_id,revision) REFERENCES inspection_product_revision(scan_id,revision),
        FOREIGN KEY(definition_version,definition_rule_id) REFERENCES catalog_rule_definition(version,rule_id),
        CHECK((rule_link_status='resolved' AND definition_version IS NOT NULL AND definition_rule_id IS NOT NULL AND definition_version=requested_rules_version AND definition_rule_id=rule_id)
           OR (rule_link_status<>'resolved' AND definition_version IS NULL AND definition_rule_id IS NULL)))""",
    """CREATE TABLE IF NOT EXISTS finding_evidence (
        scan_id TEXT NOT NULL, revision INTEGER NOT NULL, finding_id TEXT NOT NULL,
        evidence_index INTEGER NOT NULL CHECK(evidence_index>=0), evidence_kind TEXT NOT NULL,
        declaration_kind TEXT, frame_index INTEGER, reported_frame TEXT,
        availability TEXT NOT NULL CHECK(availability IN ('linked','unlocated','unknown_frame','declaration_missing','invalid_location')),
        x0 REAL, y0 REAL, x1 REAL, y1 REAL, snippet TEXT NOT NULL, confidence REAL,
        coverage_complete INTEGER NOT NULL CHECK(coverage_complete IN (0,1)),
        coverage_legible INTEGER NOT NULL CHECK(coverage_legible IN (0,1)),
        PRIMARY KEY(scan_id,revision,finding_id,evidence_index),
        FOREIGN KEY(scan_id,revision,finding_id) REFERENCES compliance_finding(scan_id,revision,finding_id),
        FOREIGN KEY(scan_id,revision,declaration_kind) REFERENCES extracted_declaration(scan_id,revision,kind),
        FOREIGN KEY(scan_id,frame_index) REFERENCES product_image(scan_id,frame_index),
        CHECK((x0 IS NULL AND y0 IS NULL AND x1 IS NULL AND y1 IS NULL)
           OR (x0 IS NOT NULL AND y0 IS NOT NULL AND x1 IS NOT NULL AND y1 IS NOT NULL AND x0>=0 AND y0>=0 AND x1>x0 AND y1>y0)),
        CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)))""",
    """CREATE TABLE IF NOT EXISTS catalog_migration_issue (
        source_kind TEXT NOT NULL, source_key TEXT NOT NULL, message TEXT NOT NULL,
        detected_at TEXT NOT NULL, resolved_at TEXT,
        PRIMARY KEY(source_kind,source_key))""",
    "CREATE INDEX IF NOT EXISTS idx_catalog_product_history ON inspection_product_revision(product_id,captured_at,revision)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_findings_rule ON compliance_finding(requested_rules_version,rule_id,machine_verdict)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_findings_review ON compliance_finding(reviewed_verdict,reviewer_id)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_evidence_image ON finding_evidence(scan_id,frame_index)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_evidence_declaration ON finding_evidence(scan_id,revision,declaration_kind)",
)


def _schema(conn):
    # executescript would commit a caller's pending transaction; execute each
    # DDL statement so upgrades and operational writes remain atomic.
    for sql in DDL:
        conn.execute(sql)
    for table in ("catalog_product", "inspection_product_revision", "catalog_rule_definition", "catalog_rule_source", "finding_evidence"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Catalog history is immutable'); END")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(compliance_finding)")
               if row[1] not in {"definition_version", "definition_rule_id", "rule_link_status"}]
    unchanged = " OR ".join(f"OLD.{column} IS NOT NEW.{column}" for column in columns)
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_compliance_finding_update BEFORE UPDATE ON compliance_finding WHEN {unchanged} BEGIN SELECT RAISE(ABORT,'Historical finding content is immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS immutable_compliance_finding_delete BEFORE DELETE ON compliance_finding BEGIN SELECT RAISE(ABORT,'Historical finding content is immutable'); END")


def _ensure_schema(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='compliance_finding'").fetchone():
        _schema(conn)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _raw(value):
    if isinstance(value, str):
        return json.loads(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _product(scan_id, reported):
    if isinstance(reported, str) and re.fullmatch(r"[0-9]{8}|[0-9]{12,14}", reported) and len(set(reported)) > 1:
        validated = check_gtin(reported)
        if validated.valid and not validated.is_restricted:
            return "gtin:" + reported.zfill(14), "gtin", reported.zfill(14), None, "GTIN length/check digit validated; issuer and product ownership not independently verified"
    return "inspection:" + scan_id, "inspection", None, scan_id, "Per-inspection identity; no validated unrestricted GTIN establishes a cross-inspection link"


def _insert(conn, table, data):
    columns = tuple(data)
    conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(data.values()))


def _transactional(fn):
    @wraps(fn)
    def run(conn, *args, **kwargs):
        if not conn.in_transaction:
            conn.execute("BEGIN")
        conn.execute("SAVEPOINT catalog_write")
        try:
            result = fn(conn, *args, **kwargs)
            conn.execute("RELEASE SAVEPOINT catalog_write")
            return result
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT catalog_write")
            conn.execute("RELEASE SAVEPOINT catalog_write")
            raise
    return run


@_transactional
def index_rule_pack(conn: sqlite3.Connection, rawpack) -> None:
    """Index a retained immutable rule_version row; never create a fake archive."""
    _ensure_schema(conn)
    supplied = _raw(rawpack)
    version = supplied["version"]
    archived = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (version,)).fetchone()
    if not archived:
        raise ValueError("Archive the exact rule pack before catalog indexing.")
    payload, digest = archived[0], archived[1]
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
        raise ValueError("The retained rule archive failed its SHA-256 integrity check.")
    retained = json.loads(payload)
    if _canonical(retained) != _canonical(supplied):
        raise ValueError("Catalog indexing requires the original archived rule content, without schema-default rewriting.")
    seen = set()
    for rule in retained.get("rules", []):
        rid = rule["id"]
        if rid in seen:
            raise ValueError("Duplicate rule identity in archived pack.")
        seen.add(rid)
        content_hash = _hash(rule)
        previous = conn.execute("SELECT definition_sha256 FROM catalog_rule_definition WHERE version=? AND rule_id=?", (version, rid)).fetchone()
        if previous:
            if previous[0] != content_hash:
                raise ValueError("An immutable catalog rule definition changed.")
            continue
        citation = rule.get("citation") or {}
        _insert(conn, "catalog_rule_definition", {
            "version": version, "rule_id": rid, "title": rule.get("title", ""),
            "act": citation.get("act", ""), "rules_name": citation.get("rules", ""),
            "clause": citation.get("clause", ""), "citation_text": citation.get("text", ""),
            "effective_from": rule.get("effective_from"), "effective_to": rule.get("effective_to"),
            "declaration_kind": rule.get("declaration"), "severity": rule.get("severity"),
            "minimum_tier": rule.get("min_assurance_tier"), "definition_json": _canonical(rule),
            "definition_sha256": content_hash,
        })
        for i, source in enumerate(rule.get("sources", [])):
            _insert(conn, "catalog_rule_source", {"version": version, "rule_id": rid, "source_index": i,
                "url": source.get("url", ""), "notification": source.get("notification"),
                "publication_date": source.get("publication_date"), "clause": source.get("clause"),
                "review_status": source.get("review_status")})
    # Enrich an explicit missing link when its actual archive becomes available.
    conn.execute("""UPDATE compliance_finding SET definition_version=requested_rules_version,
        definition_rule_id=rule_id, rule_link_status='resolved'
        WHERE requested_rules_version=? AND EXISTS (
          SELECT 1 FROM catalog_rule_definition r
          WHERE r.version=compliance_finding.requested_rules_version AND r.rule_id=compliance_finding.rule_id)""", (version,))
    conn.execute("UPDATE compliance_finding SET rule_link_status='missing_rule' WHERE requested_rules_version=? AND rule_link_status='missing_version'", (version,))


def _rule_link(conn, version, rule_id):
    if conn.execute("SELECT 1 FROM catalog_rule_definition WHERE version=? AND rule_id=?", (version, rule_id)).fetchone():
        return version, rule_id, "resolved"
    status = "missing_rule" if conn.execute("SELECT 1 FROM rule_version WHERE version=?", (version,)).fetchone() else "missing_version"
    return None, None, status


def _evidence(conn, raw, finding, revision):
    scan = raw["scan"]
    sid = scan["scan_id"]
    decls = raw.get("declarations", {})
    kinds = list(dict.fromkeys(finding.get("evidence", []) or ([finding["declaration"]] if finding.get("declaration") else ["unspecified"])))
    images = {row[1]: row[0] for row in conn.execute("SELECT frame_index,path FROM product_image WHERE scan_id=?", (sid,))}
    coverage = EvidenceCoverage.model_validate(scan.get("coverage", {}))
    complete, legible = coverage.is_complete, coverage.is_legible
    declared_kinds = {row[0] for row in conn.execute("SELECT kind FROM extracted_declaration WHERE scan_id=? AND revision=?", (sid, revision))}
    ordinal = 0
    for kind in kinds:
        declaration = decls.get(kind)
        linked_kind = kind if declaration and kind in declared_kinds else None
        if declaration:
            locators = [{"frame": declaration.get("frame"), "bbox": declaration.get("bbox"),
                         "text": declaration.get("raw", ""), "ocr_confidence": declaration.get("confidence")}]
            locators.extend(s for s in declaration.get("norm", {}).get("source_spans", []) if isinstance(s, dict))
        else:
            locators = [{"text": ""}]
        seen = set()
        for location in locators:
            frame, box = location.get("frame"), location.get("bbox")
            snippet = str(location.get("text") or "")
            key = (frame, _canonical(box), snippet)
            if key in seen:
                continue
            seen.add(key)
            bounds = [_finite(v) for v in box] if isinstance(box, (list, tuple)) and len(box) == 4 else [None] * 4
            valid_box = all(v is not None for v in bounds) and 0 <= bounds[0] < bounds[2] and 0 <= bounds[1] < bounds[3]
            if not valid_box:
                bounds = [None] * 4
            frame_index = images.get(frame)
            if declaration is None:
                status = "declaration_missing"
            elif box is not None and not valid_box:
                status = "invalid_location"
            elif not frame or not valid_box:
                status = "unlocated"
            elif frame_index is None:
                status = "unknown_frame"
            else:
                status = "linked"
            confidence = _finite(location.get("ocr_confidence"))
            if confidence is not None and not 0 <= confidence <= 1:
                confidence = None
            _insert(conn, "finding_evidence", {"scan_id": sid, "revision": revision,
                "finding_id": finding["finding_id"], "evidence_index": ordinal, "evidence_kind": kind,
                "declaration_kind": linked_kind, "frame_index": frame_index, "reported_frame": frame,
                "availability": status, "x0": bounds[0], "y0": bounds[1], "x1": bounds[2], "y1": bounds[3],
                "snippet": snippet, "confidence": confidence, "coverage_complete": int(complete),
                "coverage_legible": int(legible)})
            ordinal += 1


@_transactional
def index_analysis(conn: sqlite3.Connection, analysis) -> None:
    """Index after the retained revision, declarations and image rows exist."""
    _ensure_schema(conn)
    raw = _raw(analysis)
    _index_record(conn, raw, raw["scan"]["scan_id"], raw.get("review", {}).get("revision", 0))


def _source_relations(conn, raw, sid, revision):
    """Fill missing source indexes from retained values without replacing rows."""
    scan = raw["scan"]
    for i, path in enumerate(scan.get("frames", [])):
        conn.execute("INSERT OR IGNORE INTO product_image (scan_id,frame_index,path,sha256) VALUES (?,?,?,?)",
                     (sid, i, path, scan.get("frame_hashes", {}).get(path, "")))
    for kind, decl in raw.get("declarations", {}).items():
        conn.execute("INSERT OR IGNORE INTO extracted_declaration VALUES (?,?,?,?,?,?,?,?)",
                     (sid, revision, kind, decl.get("raw", ""), _canonical(decl.get("norm", {})),
                      decl.get("frame"), _canonical(decl.get("bbox")), _finite(decl.get("confidence"))))


def _index_record(conn, raw, sid, revision):
    if raw["scan"]["scan_id"] != sid or raw.get("review", {}).get("revision", revision) != revision:
        raise ValueError("Stored revision identity differs from its retained record.")
    prior = conn.execute("SELECT source_sha256 FROM inspection_product_revision WHERE scan_id=? AND revision=?", (sid, revision)).fetchone()
    content_hash = _hash(raw)
    if prior:
        if prior[0] != content_hash:
            raise ValueError("A catalog inspection revision cannot be overwritten with changed content.")
        return
    retained = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=?", (sid, revision)).fetchone()
    if not retained:
        raise ValueError("Retain the source inspection revision before catalog indexing.")
    if _hash(json.loads(retained[0])) != content_hash:
        raise ValueError("Catalog input differs from the retained inspection revision.")
    _source_relations(conn, raw, sid, revision)
    scan, package = raw["scan"], raw.get("package", {})
    identity = _product(sid, package.get("gtin"))
    conn.execute("INSERT OR IGNORE INTO catalog_product VALUES (?,?,?,?,?)", identity)
    decls = raw.get("declarations", {})
    def norm(kind, key):
        return decls.get(kind, {}).get("norm", {}).get(key)
    context = package.get("legal_context", {})
    category = context.get("category") if context.get("category_confirmed") is True else "unknown"
    _insert(conn, "inspection_product_revision", {
        "scan_id": sid, "revision": revision, "product_id": identity[0], "reported_gtin": package.get("gtin"),
        "brand": norm("brand", "name"), "generic_name": norm("generic_name", "name"),
        "manufacturer": decls.get("manufacturer", {}).get("raw"),
        "net_quantity": _finite(norm("net_quantity", "value_base")), "net_unit": norm("net_quantity", "unit_base"),
        "mrp": _finite(norm("retail_sale_price", "value")), "unit_price": _finite(norm("unit_sale_price", "value")),
        "category": str(category or "unknown"), "category_confirmed": int(context.get("category_confirmed") is True),
        "captured_at": scan.get("captured_at", ""), "review_status": raw.get("review", {}).get("status", "legacy_unrecorded"),
        "source_sha256": content_hash,
    })
    decisions = raw.get("review", {}).get("decisions", {})
    for finding in raw.get("findings", []):
        version = finding.get("rules_version") or raw.get("rules_version", "")
        linked_version, linked_rule, link_status = _rule_link(conn, version, finding["rule_id"])
        citation, measured = finding.get("citation", {}), finding.get("measured") or {}
        decision = decisions.get(finding["finding_id"]) or {}
        _insert(conn, "compliance_finding", {
            "scan_id": sid, "revision": revision, "finding_id": finding["finding_id"],
            "rule_id": finding["rule_id"], "requested_rules_version": version,
            "definition_version": linked_version, "definition_rule_id": linked_rule, "rule_link_status": link_status,
            "declaration_kind": finding.get("declaration"), "clause": citation.get("clause", ""),
            "citation_text": citation.get("text", ""), "machine_verdict": finding["verdict"],
            "severity": finding.get("severity", "major"), "message": finding.get("message", ""),
            "detail": finding.get("detail", ""), "tier": finding.get("tier"),
            "measured_value": _finite(measured.get("value")), "measured_uncertainty": _finite(measured.get("uncertainty")),
            "measured_unit": measured.get("unit"), "threshold": _finite(finding.get("threshold")),
            "threshold_basis": finding.get("threshold_basis"), "reviewed_verdict": decision.get("verdict"),
            "review_reason": decision.get("reason"), "reviewer_id": decision.get("actor_id"),
            "reviewed_at": decision.get("created_at"),
        })
        _evidence(conn, raw, finding, revision)


@_transactional
def upgrade_catalog(conn: sqlite3.Connection) -> None:
    """Backfill readable retained records, reporting corrupt/inconsistent gaps.

    Savepoints isolate one damaged historical record. They do not commit the
    caller's migration transaction, and source JSON bytes are never rewritten.
    """
    _schema(conn)
    def migrate(kind, key, fn):
        conn.execute("SAVEPOINT catalog_record")
        try:
            fn()
            conn.execute("RELEASE SAVEPOINT catalog_record")
            conn.execute("UPDATE catalog_migration_issue SET resolved_at=? WHERE source_kind=? AND source_key=? AND resolved_at IS NULL",
                         (datetime.now(UTC).isoformat(), kind, key))
        except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
            conn.execute("ROLLBACK TO SAVEPOINT catalog_record")
            conn.execute("RELEASE SAVEPOINT catalog_record")
            conn.execute("INSERT INTO catalog_migration_issue VALUES (?,?,?,?,NULL) ON CONFLICT(source_kind,source_key) DO UPDATE SET message=excluded.message,resolved_at=NULL",
                         (kind, key, str(exc), datetime.now(UTC).isoformat()))
    for row in conn.execute("SELECT version,record FROM rule_version").fetchall():
        migrate("rule_version", row[0], lambda row=row: index_rule_pack(conn, row[1]))
    for row in conn.execute("SELECT scan_id,revision,record FROM inspection_revision ORDER BY scan_id,revision").fetchall():
        migrate("inspection_revision", f"{row[0]}:{row[1]}",
                lambda row=row: _index_record(conn, json.loads(row[2]), row[0], row[1]))
    for row in conn.execute("SELECT scan_id,record FROM inspection").fetchall():
        def index_current(row=row):
            raw = json.loads(row[1])
            revision = raw.get("review", {}).get("revision", 0)
            if not conn.execute("SELECT 1 FROM inspection_revision WHERE scan_id=? AND revision=?", (row[0], revision)).fetchone():
                conn.execute("INSERT INTO inspection_revision VALUES (?,?,?,?,?,?,?)",
                    (row[0], revision, datetime.now(UTC).isoformat(), None, "catalog.legacy_snapshot",
                     "Retained exact existing current-record bytes; earlier revision history was unavailable.", row[1]))
            _index_record(conn, raw, row[0], revision)
        migrate("inspection", row[0], index_current)
