"""The product repository.

The problem statement asks for a repository of scanned products and inspection
history. Storing it as a time series keyed on GTIN rather than as a pile of
documents is what later makes shrinkflation detection fall out for free: same
barcode, same MRP, net quantity quietly reduced.

SQLite here, behind a narrow interface, so the Postgres/PostGIS swap in the PRD
is a driver change rather than a rewrite. Nothing outside this module writes
SQL.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.enums import DeclarationClass as DC
from ..domain.models import Analysis
from .workflow import WorkflowRepository

DEFAULT_PATH = Path("data/tula.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspection (
    scan_id        TEXT PRIMARY KEY,
    captured_at    TEXT NOT NULL,
    packing_date   TEXT,
    lane           TEXT,
    operator       TEXT,
    tier           TEXT,
    rules_version  TEXT,
    engine         TEXT,
    overall        TEXT,
    gtin           TEXT,
    brand          TEXT,
    generic_name   TEXT,
    manufacturer   TEXT,
    net_quantity   REAL,
    net_unit       TEXT,
    mrp            REAL,
    unit_price     REAL,
    n_violations   INTEGER DEFAULT 0,
    n_inconclusive INTEGER DEFAULT 0,
    record         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id   TEXT NOT NULL REFERENCES inspection(scan_id) ON DELETE CASCADE,
    rule_id   TEXT NOT NULL,
    clause    TEXT,
    verdict   TEXT NOT NULL,
    severity  TEXT,
    message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_finding_scan ON finding(scan_id);
CREATE INDEX IF NOT EXISTS idx_finding_rule ON finding(rule_id, verdict);
CREATE INDEX IF NOT EXISTS idx_inspection_gtin ON inspection(gtin, captured_at);
"""


@dataclass
class Row:
    """A repository search hit, flattened for display."""

    scan_id: str
    captured_at: str
    brand: str | None
    generic_name: str | None
    gtin: str | None
    net_quantity: float | None
    net_unit: str | None
    mrp: float | None
    overall: str
    tier: str
    n_violations: int
    n_inconclusive: int
    operator: str | None = None
    category: str = "unknown"
    review_status: str = "draft"
    product_status: str = "NEEDS_REVIEW"
    source: str = "inspection"
    region: str = ""


class Repository(WorkflowRepository):
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        self._upgrade()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------

    def save(self, analysis: Analysis, *, connection=None) -> str:
        """Save once, optionally within the caller's existing transaction."""
        decls = analysis.declarations
        nq = decls.get(DC.NET_QUANTITY)
        mrp = decls.get(DC.RETAIL_SALE_PRICE)
        up = decls.get(DC.UNIT_SALE_PRICE)
        brand = decls.get(DC.BRAND)
        generic = decls.get(DC.GENERIC_NAME)
        manufacturer = decls.get(DC.MANUFACTURER)

        with (self._connect() if connection is None else nullcontext(connection)) as conn:
            existing = conn.execute("SELECT record FROM inspection WHERE scan_id=?",
                                    (analysis.scan.scan_id,)).fetchone()
            if existing:
                if Analysis.model_validate_json(existing["record"]) == analysis:
                    return analysis.scan.scan_id
                raise ValueError("An inspection already exists. Use an audited revision to change it.")
            conn.execute(
                """
                INSERT INTO inspection (
                    scan_id, captured_at, packing_date, lane, operator, tier,
                    rules_version, engine, overall, gtin, brand, generic_name,
                    manufacturer, net_quantity, net_unit, mrp, unit_price,
                    n_violations, n_inconclusive, record, latitude, longitude
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    analysis.scan.scan_id,
                    analysis.scan.captured_at.isoformat(),
                    str(analysis.scan.packing_date) if analysis.scan.packing_date else None,
                    analysis.scan.lane.value,
                    analysis.scan.operator,
                    analysis.scan.tier.value,
                    analysis.rules_version,
                    analysis.engine,
                    analysis.overall.value,
                    analysis.package.gtin,
                    (brand.norm.get("name") if brand else None),
                    (generic.norm.get("name") if generic else None),
                    (manufacturer.raw.split("\n")[0] if manufacturer else None),
                    (nq.norm.get("value_base") if nq else None),
                    (nq.norm.get("unit_base") if nq else None),
                    (mrp.norm.get("value") if mrp else None),
                    (up.norm.get("value") if up else None),
                    len(analysis.violations),
                    len(analysis.pending_review),
                    json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
                    *(analysis.scan.geo or (None, None)),
                ),
            )
            conn.execute("DELETE FROM finding WHERE scan_id = ?", (analysis.scan.scan_id,))
            conn.executemany(
                "INSERT INTO finding (scan_id, rule_id, clause, verdict, severity, message)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (
                        analysis.scan.scan_id,
                        f.rule_id,
                        f.citation.clause,
                        f.verdict.value,
                        f.severity.value,
                        f.message,
                    )
                    for f in analysis.findings
                ],
            )
            self._index_analysis(conn, analysis)
        return analysis.scan.scan_id

    def get(self, scan_id: str) -> Analysis | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record FROM inspection WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        if row is None:
            return None
        return Analysis.model_validate(json.loads(row["record"]))

    def search(
        self,
        query: str = "",
        *,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[Row]:
        clauses, params = [], []
        if query:
            like = f"%{query.lower()}%"
            clauses.append(
                "(LOWER(COALESCE(brand,'')) LIKE ? OR LOWER(COALESCE(generic_name,'')) LIKE ?"
                " OR COALESCE(gtin,'') LIKE ? OR LOWER(COALESCE(manufacturer,'')) LIKE ?"
                " OR LOWER(scan_id) LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if verdict:
            clauses.append("overall = ?")
            params.append(verdict)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT scan_id, captured_at, brand, generic_name, gtin, net_quantity,"
                f" net_unit, mrp, overall, tier, n_violations, n_inconclusive"
                f" FROM inspection {where} ORDER BY captured_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [Row(**dict(r)) for r in rows]

    def history(self, gtin: str, *, source: str | None = None) -> list[dict[str, Any]]:
        """Every scan of one product, oldest first.

        This is the series a shrinkflation check runs over: constant MRP with a
        falling net quantity is a finding no single scan could ever produce.
        """
        if source is not None and source not in ("inspection", "bench"):
            raise ValueError("Choose operational inspections or test-bench runs.")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT scan_id, captured_at, net_quantity, net_unit, mrp, unit_price,"
                " overall, n_violations,review_status,product_status,source,rules_version FROM inspection WHERE gtin = ?" +
                (" AND source=?" if source is not None else "") + " ORDER BY julianday(captured_at),scan_id",
                (gtin, source) if source is not None else (gtin,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        """Dashboard aggregates."""
        with self._connect() as conn:
            totals = conn.execute(
                "SELECT COUNT(*) AS inspections,"
                " SUM(n_violations) AS violations,"
                " SUM(CASE WHEN overall='VIOLATION' THEN 1 ELSE 0 END) AS failing,"
                " SUM(CASE WHEN overall='INCONCLUSIVE' THEN 1 ELSE 0 END) AS unresolved"
                " FROM inspection"
            ).fetchone()
            by_rule = conn.execute(
                "SELECT rule_id, clause, message, COUNT(*) AS n FROM finding"
                " WHERE verdict='VIOLATION' GROUP BY rule_id ORDER BY n DESC LIMIT 12"
            ).fetchall()
            by_tier = conn.execute(
                "SELECT tier, COUNT(*) AS n FROM inspection GROUP BY tier ORDER BY tier"
            ).fetchall()
            by_brand = conn.execute(
                "SELECT COALESCE(brand,'unidentified') AS brand,"
                " COUNT(*) AS scans, SUM(n_violations) AS violations"
                " FROM inspection GROUP BY brand"
                " HAVING violations > 0 ORDER BY violations DESC LIMIT 10"
            ).fetchall()

        total = totals["inspections"] or 0
        failing = totals["failing"] or 0
        return {
            "inspections": total,
            "violations": totals["violations"] or 0,
            "failing": failing,
            "unresolved": totals["unresolved"] or 0,
            "violation_rate": (failing / total * 100.0) if total else 0.0,
            "by_rule": [dict(r) for r in by_rule],
            "by_tier": [dict(r) for r in by_tier],
            "by_brand": [dict(r) for r in by_brand],
        }


def detect_shrinkflation(history: list[dict[str, Any]]) -> list[str]:
    """Adverse steps in the (net quantity, MRP) series for one GTIN."""
    notes: list[str] = []
    previous = None
    for entry in history:
        if previous and entry.get("net_unit") and entry.get("net_unit") == previous.get("net_unit") and entry["net_quantity"] and previous["net_quantity"]:
            drop = previous["net_quantity"] - entry["net_quantity"]
            if drop > 0.005 * previous["net_quantity"]:
                same_price = (
                    entry["mrp"] is not None
                    and previous["mrp"] is not None
                    and abs(entry["mrp"] - previous["mrp"]) < 0.01
                )
                pct = drop / previous["net_quantity"] * 100.0
                when = str(entry["captured_at"])[:10]
                notes.append(
                    f"{when}: net quantity fell from {previous['net_quantity']:g} to "
                    f"{entry['net_quantity']:g} {entry['net_unit'] or ''} "
                    f"({pct:.1f}% less)"
                    + (
                        f" while the retail sale price stayed at Rs {entry['mrp']:g}."
                        if same_price
                        else "."
                    )
                )
        previous = entry
    return notes
