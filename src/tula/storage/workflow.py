"""Relational inspection revisions, review history and searchable operational data."""
from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..domain.enums import DeclarationClass as DC
from ..domain.enums import Verdict
from ..domain.models import Analysis, validated_geo


class RevisionConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


def _contains(value):
    """Treat user-entered percent/underscore/backslash as literal search text."""
    return "%" + str(value).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _category(analysis):
    context = analysis.package.legal_context
    confirmed = context.get("category")
    if context.get("category_confirmed") is True and isinstance(confirmed, str) and confirmed != "unknown":
        return confirmed, "officer_confirmed"
    category = analysis.intelligence.get("product", {}).get("category", "unknown")
    if isinstance(category, dict):
        category = category.get("value", "unknown")
    category = str(category or "unknown")
    return category, "ocr_suggested" if category != "unknown" else "unknown"


# Search choices cover officer-confirmed package context and older OCR labels.
# Other retained labels are added at read time; history is never reclassified.
CATEGORY_LABELS = {
    "general": "General packaged commodity", "food": "Food", "alcohol": "Alcohol",
    "tobacco": "Tobacco", "pan_masala": "Pan masala", "medical_device": "Medical device",
    "cosmetic": "Cosmetic", "seed": "Seed", "personal_care": "Personal care",
    "household": "Household", "industrial": "Industrial", "unknown": "Unknown / not confirmed",
}


def _date_range(start, end):
    if start:
        date.fromisoformat(start)
    if end:
        date.fromisoformat(end)
    if start and end and start > end:
        raise ValueError("The From date must be on or before the To date.")


def _coordinate_group(latitude, longitude):
    """Validate an optional pair and return SQLite-compatible 0.001° centres."""
    latitude_missing = latitude is None or str(latitude).strip() == ""
    longitude_missing = longitude is None or str(longitude).strip() == ""
    if latitude_missing and longitude_missing:
        return None
    if latitude_missing or longitude_missing:
        raise ValueError("Enter both latitude and longitude coordinate filters.")
    try:
        checked = validated_geo([float(Decimal(str(latitude))), float(Decimal(str(longitude)))])
        assert checked is not None
        return tuple(
            float(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
            for value in checked
        )
    except (AssertionError, InvalidOperation, OverflowError, TypeError, ValueError):
        raise ValueError("Enter a valid latitude and longitude coordinate group.") from None


class WorkflowRepository:
    def _upgrade(self):
        with self._connect() as conn:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(inspection)")}
            additions = {"category": "TEXT NOT NULL DEFAULT 'unknown'",
                         "category_basis": "TEXT NOT NULL DEFAULT 'unknown'",
                         "review_status": "TEXT NOT NULL DEFAULT 'draft'",
                         "product_status": "TEXT NOT NULL DEFAULT 'NEEDS_REVIEW'",
                         "source": "TEXT NOT NULL DEFAULT 'inspection'",
                         "region": "TEXT NOT NULL DEFAULT ''",
                         "latitude": "REAL", "longitude": "REAL",
                         "inspector_id": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 0",
                         "parent_scan_id": "TEXT REFERENCES inspection(scan_id)"}
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE inspection ADD COLUMN {name} {declaration}")
            if "category_basis" not in columns:
                for row in conn.execute("SELECT scan_id,record FROM inspection").fetchall():
                    category, basis = _category(Analysis.model_validate_json(row["record"]))
                    conn.execute("UPDATE inspection SET category=?,category_basis=? WHERE scan_id=?",
                                 (category, basis, row["scan_id"]))
            if "latitude" not in columns or "longitude" not in columns:
                for row in conn.execute("SELECT scan_id,record FROM inspection").fetchall():
                    try:
                        stored = json.loads(row["record"])
                        geo = validated_geo(stored.get("scan", {}).get("geo"))
                    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                        # Older/minimal rows remain searchable and simply have no
                        # plottable coordinate. Migration must not rewrite evidence.
                        geo = None
                    conn.execute("UPDATE inspection SET latitude=?,longitude=? WHERE scan_id=?",
                                 (*geo, row["scan_id"]) if geo else (None, None, row["scan_id"]))
            finding_columns = {r["name"] for r in conn.execute("PRAGMA table_info(finding)")}
            if "citation_text" not in finding_columns:
                conn.execute("ALTER TABLE finding ADD COLUMN citation_text TEXT NOT NULL DEFAULT ''")
                for row in conn.execute("SELECT scan_id, record FROM inspection").fetchall():
                    original = json.loads(row["record"])
                    conn.executemany("UPDATE finding SET citation_text=? WHERE scan_id=? AND rule_id=?",
                                     [(f.get("citation", {}).get("text") or "", row["scan_id"], f["rule_id"])
                                      for f in original.get("findings", [])])
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS inspection_revision (
                    scan_id TEXT NOT NULL REFERENCES inspection(scan_id), revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, actor_id TEXT, action TEXT NOT NULL,
                    reason TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(scan_id,revision));
                CREATE TABLE IF NOT EXISTS product_image (
                    scan_id TEXT NOT NULL REFERENCES inspection(scan_id), frame_index INTEGER NOT NULL,
                    path TEXT NOT NULL, sha256 TEXT NOT NULL, original_path TEXT, original_sha256 TEXT,
                    PRIMARY KEY(scan_id,frame_index));
                CREATE TABLE IF NOT EXISTS ocr_result (
                    scan_id TEXT NOT NULL REFERENCES inspection(scan_id), line_index INTEGER NOT NULL,
                    frame TEXT, text TEXT NOT NULL, bbox TEXT, confidence REAL NOT NULL,
                    PRIMARY KEY(scan_id,line_index));
                CREATE TABLE IF NOT EXISTS extracted_declaration (
                    scan_id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
                    raw TEXT NOT NULL, normalized TEXT NOT NULL, frame TEXT, bbox TEXT, confidence REAL,
                    PRIMARY KEY(scan_id,revision,kind),
                    FOREIGN KEY(scan_id,revision) REFERENCES inspection_revision(scan_id,revision));
                CREATE TABLE IF NOT EXISTS inspector_decision (
                    scan_id TEXT NOT NULL, revision INTEGER NOT NULL, finding_id TEXT NOT NULL,
                    verdict TEXT NOT NULL, reason TEXT NOT NULL, actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(scan_id,revision,finding_id),
                    FOREIGN KEY(scan_id,revision) REFERENCES inspection_revision(scan_id,revision));
                CREATE TABLE IF NOT EXISTS report_artifact (
                    id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    format TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                    actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(scan_id,revision) REFERENCES inspection_revision(scan_id,revision));
                CREATE TABLE IF NOT EXISTS rule_version (
                    version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, record TEXT NOT NULL,
                    created_at TEXT NOT NULL, actor_id TEXT, note TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS idx_inspection_filters
                    ON inspection(source,review_status,captured_at);
                CREATE INDEX IF NOT EXISTS idx_inspection_product
                    ON inspection(category,product_status,manufacturer);
                CREATE INDEX IF NOT EXISTS idx_inspection_operator ON inspection(inspector_id,captured_at);
                CREATE INDEX IF NOT EXISTS idx_inspection_parent ON inspection(parent_scan_id,captured_at);
                CREATE INDEX IF NOT EXISTS idx_inspection_time
                    ON inspection(julianday(captured_at) DESC,scan_id DESC);
                CREATE INDEX IF NOT EXISTS idx_inspection_source_time
                    ON inspection(source,julianday(captured_at) DESC,scan_id DESC);
                CREATE INDEX IF NOT EXISTS idx_inspection_day
                    ON inspection(date(captured_at));
                CREATE INDEX IF NOT EXISTS idx_inspection_source_day
                    ON inspection(source,date(captured_at));
                CREATE INDEX IF NOT EXISTS idx_inspection_source_category_day
                    ON inspection(source,category,date(captured_at));
                CREATE INDEX IF NOT EXISTS idx_inspection_source_geo
                    ON inspection(source,latitude,longitude)
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_inspection_source_geo_recent
                    ON inspection(source,ROUND(latitude,3),ROUND(longitude,3),
                                  julianday(captured_at) DESC,scan_id DESC,latitude,longitude)
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;
            """)
            # Migration enriches indexes only; historical machine records stay byte-for-byte intact.
            for row in conn.execute("SELECT scan_id, record FROM inspection WHERE scan_id NOT IN "
                                    "(SELECT scan_id FROM inspection_revision)").fetchall():
                analysis = Analysis.model_validate_json(row["record"])
                if any("bench" in f.replace("\\", "/").split("/") for f in analysis.scan.frames):
                    analysis.scan.source = "bench"
                self._index_analysis(conn, analysis)
            from .catalog import upgrade_catalog
            upgrade_catalog(conn)

    def _index_analysis(self, conn, analysis, *, action="inspection.created", actor_id=None, reason=""):
        scan, review = analysis.scan, analysis.review
        conn.executemany("UPDATE finding SET citation_text=? WHERE scan_id=? AND rule_id=?",
                         [(f.citation.text or "", scan.scan_id, f.rule_id) for f in analysis.findings])
        category, basis = _category(analysis)
        conn.execute("UPDATE inspection SET category=?,category_basis=?,review_status=?,product_status=?,source=?,"
                     "region=?,latitude=?,longitude=?,inspector_id=?,revision=?,parent_scan_id=? WHERE scan_id=?",
                     (category, basis, review.status, analysis.product_status, scan.source,
                      scan.region, *(scan.geo or (None, None)), scan.inspector_id, review.revision,
                      scan.parent_scan_id, scan.scan_id))
        conn.execute("INSERT OR IGNORE INTO inspection_revision VALUES (?,?,?,?,?,?,?)",
                     (scan.scan_id, review.revision, datetime.now(UTC).isoformat(), actor_id or scan.inspector_id,
                      action, reason, analysis.model_dump_json()))
        original_paths = {edit["frame"]: edit.get("original") for edit in scan.capture_edits if edit.get("frame")}
        for i, path in enumerate(scan.frames):
            original_path = original_paths.get(path)
            if original_path not in scan.original_frame_hashes:
                original_path = path if path in scan.original_frame_hashes else None
            original = (original_path, scan.original_frame_hashes.get(original_path))
            conn.execute("INSERT OR IGNORE INTO product_image VALUES (?,?,?,?,?,?)",
                         (scan.scan_id, i, path, scan.frame_hashes.get(path, ""), *original))
        for i, span in enumerate(analysis.spans):
            conn.execute("INSERT OR IGNORE INTO ocr_result VALUES (?,?,?,?,?,?)",
                         (scan.scan_id, i, span.frame, span.text, _json(span.bbox), span.confidence))
        for kind, d in analysis.declarations.items():
            conn.execute("INSERT OR IGNORE INTO extracted_declaration VALUES (?,?,?,?,?,?,?,?)",
                         (scan.scan_id, review.revision, kind.value, d.raw, _json(d.norm), d.frame,
                          _json(d.bbox), d.confidence))
        for fid, d in review.decisions.items():
            conn.execute("INSERT OR IGNORE INTO inspector_decision VALUES (?,?,?,?,?,?,?)",
                         (scan.scan_id, review.revision, fid, d.verdict.value, d.reason,
                          d.actor_id, d.created_at.isoformat()))
        from .catalog import index_analysis
        index_analysis(conn, analysis)

    def revise(self, scan_id, expected_revision, actor_id, action, reason, change):
        """Optimistic check and complete revision written under a single write transaction."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM inspection WHERE scan_id=?", (scan_id,)).fetchone()
            if not row:
                raise KeyError(scan_id)
            analysis = Analysis.model_validate_json(row["record"])
            if analysis.review.revision != expected_revision:
                raise RevisionConflict("This inspection changed in another session. Reload before saving.")
            change(analysis)
            analysis.review.revision += 1
            nq = analysis.declarations.get(DC.NET_QUANTITY)
            mrp = analysis.declarations.get(DC.RETAIL_SALE_PRICE)
            unit_price = analysis.declarations.get(DC.UNIT_SALE_PRICE)
            brand = analysis.declarations.get(DC.BRAND)
            generic = analysis.declarations.get(DC.GENERIC_NAME)
            manufacturer = analysis.declarations.get(DC.MANUFACTURER)
            conn.execute("UPDATE inspection SET record=?,overall=?,n_violations=?,n_inconclusive=?,"
                         "net_quantity=?,net_unit=?,mrp=?,brand=?,generic_name=?,manufacturer=?,unit_price=?,packing_date=? WHERE scan_id=?",
                         (analysis.model_dump_json(), analysis.overall.value, len(analysis.violations),
                          len(analysis.pending_review), nq.norm.get("value_base") if nq else None,
                          nq.norm.get("unit_base") if nq else None, mrp.norm.get("value") if mrp else None,
                          brand.norm.get("name") if brand else None, generic.norm.get("name") if generic else None,
                          manufacturer.raw.split("\n")[0] if manufacturer else None,
                          unit_price.norm.get("value") if unit_price else None,
                          str(analysis.scan.packing_date) if analysis.scan.packing_date else None, scan_id))
            conn.execute("DELETE FROM finding WHERE scan_id=?", (scan_id,))
            conn.executemany("INSERT INTO finding (scan_id,rule_id,clause,verdict,severity,message) "
                             "VALUES (?,?,?,?,?,?)", [(scan_id, f.rule_id, f.citation.clause, f.verdict.value,
                                                      f.severity.value, f.message) for f in analysis.findings])
            self._index_analysis(conn, analysis, action=action, actor_id=actor_id, reason=reason)
        return analysis

    def revisions(self, scan_id):
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT revision,created_at,actor_id,action,reason "
                    "FROM inspection_revision WHERE scan_id=? ORDER BY revision DESC", (scan_id,))]

    def revision(self, scan_id, number):
        with self._connect() as conn:
            row = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=?",
                               (scan_id, number)).fetchone()
        return Analysis.model_validate_json(row["record"]) if row else None

    def archive_rules(self, pack, *, actor_id=None, note="", connection=None):
        """Archive atomically with an optional caller-owned audit transaction."""
        if connection is None:
            with self._connect() as conn:
                return self.archive_rules(pack, actor_id=actor_id, note=note, connection=conn)
        import hashlib

        from ..rules.spec import RulePack

        payload = pack.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        prior = connection.execute("SELECT sha256,record FROM rule_version WHERE version=?", (pack.version,)).fetchone()
        if prior:
            if hashlib.sha256(prior["record"].encode()).hexdigest() != prior["sha256"]:
                raise ValueError("The archived rule content failed its integrity check.")
            # Verify retained bytes before parsing. New optional schema defaults
            # must not rewrite an unchanged historical pack or its original hash.
            if prior["sha256"] != digest and RulePack.model_validate_json(prior["record"]) != pack:
                raise ValueError("Rule content changed without a new version identifier.")
            from .catalog import index_rule_pack
            index_rule_pack(connection, prior["record"])
            return
        connection.execute("INSERT INTO rule_version VALUES (?,?,?,?,?,?)",
                           (pack.version, digest, payload, datetime.now(UTC).isoformat(), actor_id, note))
        from .catalog import index_rule_pack
        index_rule_pack(connection, payload)

    def archived_rules(self, version):
        import hashlib
        with self._connect() as conn:
            row = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (version,)).fetchone()
        if row and hashlib.sha256(row["record"].encode()).hexdigest() != row["sha256"]:
            raise ValueError("The archived rule content failed its integrity check.")
        return json.loads(row["record"]) if row else None

    def filter_search(self, query="", *, page=1, page_size=25, **filters):
        _date_range(filters.get("date_from", ""), filters.get("date_to", ""))
        coordinate_group = _coordinate_group(filters.get("geo_lat", ""), filters.get("geo_lon", ""))
        finding_outcome = filters.get("finding_outcome", "")
        if finding_outcome and finding_outcome not in {"FLAGGED", *(verdict.value for verdict in Verdict)}:
            raise ValueError("Choose a valid finding outcome.")
        clauses, values = [], []
        if query.strip():
            from dateutil import parser
            q = query.strip().lower()
            # Month/year searches are deterministic only when an explicit month name + year exists.
            import re
            if re.fullmatch(r"[a-z]+\s+\d{4}", q):
                try:
                    parsed = parser.parse(q, default=datetime(2000, 1, 1, tzinfo=UTC))
                    first = date(parsed.year, parsed.month, 1)
                    following = date(parsed.year + (parsed.month == 12), parsed.month % 12 + 1, 1)
                    clauses.append("date(i.captured_at)>=? AND date(i.captured_at)<?")
                    values.extend([first.isoformat(), following.isoformat()])
                    q = ""
                except (ValueError, OverflowError):
                    pass
            if q:
                # A cited rule number must match the citation, not a GTIN or price.
                # The numeric boundary keeps Rule 7 distinct from Rule 70.
                def cited_rule(match):
                    needle = "*rule" + match.group(1)
                    clauses.append("EXISTS (SELECT 1 FROM finding f WHERE f.scan_id=i.scan_id AND "
                                   "(LOWER(REPLACE(COALESCE(f.clause,''),' ','')) GLOB ? OR "
                                   "LOWER(REPLACE(COALESCE(f.clause,''),' ','')) GLOB ?))")
                    values.extend([needle, needle + "[^0-9]*"])
                    return " "

                q = re.sub(r"\brules?\s*(\d+(?:\([a-z0-9]+\))*)", cited_rule, q)
                terms = q.split()[:12]
                for term in terms:
                    clauses.append("(LOWER(COALESCE(i.brand,'')||' '||COALESCE(i.generic_name,'')||' '||"
                                   "COALESCE(i.manufacturer,'')||' '||COALESCE(i.gtin,'')||' '||i.scan_id||' '||"
                                   "COALESCE(i.operator,'')||' '||i.category||' '||i.overall) LIKE ? ESCAPE '\\' OR EXISTS "
                                   "(SELECT 1 FROM finding f WHERE f.scan_id=i.scan_id AND LOWER(f.rule_id||' '||"
                                   "COALESCE(f.clause,'')||' '||COALESCE(f.message,'')||' '||f.citation_text||' '||"
                                   "f.verdict) LIKE ? ESCAPE '\\'))")
                    values.extend([_contains(term)] * 2)
        for field in ("category", "review_status", "product_status", "source", "region", "inspector_id", "overall"):
            if filters.get(field):
                clauses.append(f"i.{field}=?")
                values.append(filters[field])
        for field in ("operator", "manufacturer"):
            if filters.get(field):
                clauses.append(f"LOWER(i.{field}) LIKE ? ESCAPE '\\'")
                values.append(_contains(filters[field]))
        for field, sign in (("date_from", ">="), ("date_to", "<=")):
            if filters.get(field):
                date.fromisoformat(filters[field])
                clauses.append(f"date(i.captured_at){sign}?")
                values.append(filters[field])
        if coordinate_group:
            latitude, longitude = coordinate_group
            clauses.extend((
                "i.latitude IS NOT NULL AND i.longitude IS NOT NULL",
                "ROUND(i.latitude,3)=?",
                "ROUND(i.longitude,3)=?",
            ))
            values.extend((latitude, longitude))
        if filters.get("rule_id") or finding_outcome:
            finding_clauses = ["f.scan_id=i.scan_id"]
            if filters.get("rule_id"):
                finding_clauses.append("f.rule_id=?")
                values.append(filters["rule_id"])
            if finding_outcome == "FLAGGED":
                finding_clauses.append("f.verdict IN ('VIOLATION','ADVISORY')")
            elif finding_outcome:
                finding_clauses.append("f.verdict=?")
                values.append(finding_outcome)
            # Both conditions must describe the same finding, not two different rules.
            clauses.append("EXISTS (SELECT 1 FROM finding f WHERE " + " AND ".join(finding_clauses) + ")")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        page_size, page = max(1, min(100, page_size)), max(1, page)
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM inspection i" + where, values).fetchone()[0]
            rows = [dict(r) for r in conn.execute(
                "SELECT i.*,strftime('%Y-%m-%dT%H:%M:%SZ',i.captured_at) AS captured_utc FROM inspection i" +
                where + " ORDER BY julianday(captured_at) DESC,scan_id DESC LIMIT ? OFFSET ?",
                [*values, page_size, (page-1)*page_size])]
            categories = dict(CATEGORY_LABELS)
            retained = {r[0] for r in conn.execute("SELECT DISTINCT category FROM inspection") if r[0]}
            if filters.get("category"):
                retained.add(filters["category"])
            for category in sorted(retained - categories.keys(), key=str.casefold):
                categories[category] = category.replace("_", " ").capitalize() + " (other category)"
        for row in rows:
            row.pop("record", None)
        return {"rows": rows, "total": total, "page": page, "pages": max(1, (total+page_size-1)//page_size),
                "page_size": page_size,
                "category_choices": [{"value": value, "label": label} for value, label in categories.items()]}

    def analytics(self, *, source="inspection", date_from="", date_to=""):
        if source not in ("inspection", "bench"):
            raise ValueError("Choose operational inspections or test-bench runs.")
        _date_range(date_from, date_to)
        filters = ["i.source=?"]
        values = [source]
        for value, sign in ((date_from, ">="), (date_to, "<=")):
            if value:
                date.fromisoformat(value)
                filters.append(f"date(i.captured_at){sign}?")
                values.append(value)
        where = " WHERE " + " AND ".join(filters)
        with self._connect() as conn:
            totals = dict(conn.execute("SELECT COUNT(*) AS inspections, "
                "COALESCE(SUM(i.n_violations),0) AS potential_violations, "
                "COALESCE(SUM(i.product_status='COMPLIANT'),0) AS compliant, "
                "COALESCE(SUM(i.product_status='NON_COMPLIANT'),0) AS non_compliant, "
                "COALESCE(SUM(i.product_status='NEEDS_REVIEW'),0) AS needs_review, "
                "COALESCE(SUM(i.review_status='approved'),0) AS approved, "
                "COALESCE(SUM(date(i.captured_at) BETWEEN ? AND ?),0) AS this_week, "
                "COALESCE(SUM(strftime('%Y-%m',i.captured_at)=? AND date(i.captured_at)<=?),0) AS this_month "
                "FROM inspection i" + where,
                [(datetime.now(UTC)-timedelta(days=6)).date().isoformat(), datetime.now(UTC).date().isoformat(),
                 datetime.now(UTC).strftime("%Y-%m"), datetime.now(UTC).date().isoformat(),
                 *values]).fetchone())
            def grouped(column, alias, limit=12):
                return [dict(r) for r in conn.execute(
                    f"SELECT COALESCE(NULLIF({column},''),'Unspecified') AS {alias},COUNT(*) AS n,"
                    "SUM(i.n_violations) AS potential FROM inspection i"+where+
                    f" GROUP BY {column} ORDER BY n DESC LIMIT ?", [*values, limit])]
            totals.update(categories=grouped("i.category", "label"), manufacturers=grouped("i.manufacturer", "label"),
                          activity=grouped("i.operator", "label"), regions=grouped("i.region", "label"),
                          category_basis=grouped("i.category_basis", "label"))
            totals["trend"] = [dict(r) for r in conn.execute(
                "SELECT date(i.captured_at) AS day,COUNT(*) AS total,"
                "SUM(i.product_status='COMPLIANT') AS compliant,SUM(i.product_status='NON_COMPLIANT') AS non_compliant "
                "FROM inspection i"+where+" GROUP BY day ORDER BY day DESC LIMIT 30", values)][::-1]
            for key, column in (("by_rule", "f.rule_id"), ("severity", "f.severity")):
                outcomes = "f.verdict IN ('VIOLATION','ADVISORY')" if key == "by_rule" else "f.verdict='VIOLATION'"
                totals[key] = [dict(r) for r in conn.execute(
                    f"SELECT {column} AS label,COUNT(*) AS n FROM finding f JOIN inspection i USING(scan_id)"+
                    where+f" AND {outcomes} GROUP BY {column} ORDER BY n DESC", values)]
            totals["verified_violations"] = conn.execute(
                "SELECT COUNT(*) FROM inspector_decision d JOIN inspection i ON i.scan_id=d.scan_id AND "
                "i.revision=d.revision"+where+" AND d.verdict='VIOLATION'", values).fetchone()[0]
            coordinate_rows = [dict(row) for row in conn.execute(
                "SELECT ROUND(i.latitude,3) AS latitude,ROUND(i.longitude,3) AS longitude,"
                "COUNT(*) AS n,COALESCE(SUM(i.n_violations),0) AS potential,"
                "MIN(NULLIF(i.region,'')) AS label,COUNT(DISTINCT NULLIF(i.region,'')) AS region_count "
                "FROM inspection i"+where+" AND i.latitude IS NOT NULL AND i.longitude IS NOT NULL "
                "GROUP BY ROUND(i.latitude,3),ROUND(i.longitude,3) ORDER BY n DESC,latitude,longitude LIMIT 200",
                values)]
            coordinate_counts = dict(conn.execute(
                "SELECT COUNT(*) AS total,COALESCE(SUM(i.latitude BETWEEN 6 AND 38 "
                "AND i.longitude BETWEEN 68 AND 98),0) AS mapped FROM inspection i"+where+
                " AND i.latitude IS NOT NULL AND i.longitude IS NOT NULL", values).fetchone())
        denominator = totals["compliant"] + totals["non_compliant"]
        totals["compliance_percentage"] = round(100*totals["compliant"]/denominator, 1) if denominator else None
        totals["compliance_trend"] = [dict(row,
            day_number=date.fromisoformat(row["day"]).toordinal(),
            assessed=row["compliant"]+row["non_compliant"],
            excluded=row["total"]-row["compliant"]-row["non_compliant"],
            percentage=round(100*row["compliant"]/(row["compliant"]+row["non_compliant"]), 1)
                if row["compliant"]+row["non_compliant"] else None) for row in totals["trend"]]
        for row in coordinate_rows:
            latitude, longitude = row["latitude"], row["longitude"]
            row["within_india_extent"] = 6 <= latitude <= 38 and 68 <= longitude <= 98
            if row["within_india_extent"]:
                row["x"] = round(70 + (longitude - 68) / 30 * 600, 2)
                row["y"] = round(450 - (latitude - 6) / 32 * 400, 2)
                row["radius"] = round(5 + min(12, math.sqrt(row["n"]) * 2), 2)
            if row["region_count"] > 1:
                row["label"] = f"{row['label'] or 'Unspecified'} and {row['region_count'] - 1} other location(s)"
            else:
                row["label"] = row["label"] or "Unspecified"
        totals["geo_points"] = coordinate_rows
        totals["mapped_geo_points"] = [row for row in coordinate_rows if row["within_india_extent"]]
        totals["geo_record_count"] = coordinate_counts["total"]
        totals["mapped_geo_record_count"] = coordinate_counts["mapped"]
        totals["geo_groups_limited"] = sum(row["n"] for row in coordinate_rows) < coordinate_counts["total"]
        return totals
