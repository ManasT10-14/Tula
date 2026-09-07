"""Bounded, read-only navigation between retained package captures."""
from __future__ import annotations

import hashlib

from ..domain.models import Analysis


def _parent_assertions(conn, analysis):
    scan = analysis.scan
    revision, digest = scan.parent_revision, scan.parent_record_sha256
    if not scan.parent_scan_id or revision is None or not digest:
        return None, "not_recorded"
    row = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=?",
                       (scan.parent_scan_id, revision)).fetchone()
    if not row or hashlib.sha256(row["record"].encode()).hexdigest() != digest:
        return None, "unavailable"
    try:
        parent = Analysis.model_validate_json(row["record"])
    except ValueError:
        return None, "unavailable"
    if parent.scan.scan_id != scan.parent_scan_id or parent.review.revision != revision:
        return None, "unavailable"
    corrections = []
    for item in parent.review.corrections[-20:]:
        after = item.get("after", {})
        corrections.append({"kind": str(item.get("kind", "Declaration")).replace("intelligence.", ""),
            "raw": str(after.get("raw", "")) if isinstance(after, dict) else "",
            "actor": str(item.get("actor", "Not recorded")),
            "reason": str(item.get("reason", ""))})
    return {"revision": revision, "record_sha256": digest, "rules_version": parent.rules_version,
            "corrections": corrections, "correction_count": len(parent.review.corrections)}, "verified"


def linked_captures(repo, analysis, *, limit=20):
    """Return direct links, without treating newer captures as approved replacements."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Choose between 1 and 100 linked captures.")
    columns = "scan_id,captured_at,operator,review_status,product_status,rules_version,revision"
    with repo._connect() as conn:
        parent = None
        if analysis.scan.parent_scan_id:
            parent = conn.execute(f"SELECT {columns} FROM inspection WHERE scan_id=?",
                                  (analysis.scan.parent_scan_id,)).fetchone()
        total = conn.execute("SELECT COUNT(*) FROM inspection WHERE parent_scan_id=?",
                             (analysis.scan.scan_id,)).fetchone()[0]
        rows = conn.execute(f"SELECT {columns} FROM inspection WHERE parent_scan_id=? "
                            "ORDER BY captured_at DESC,scan_id DESC LIMIT ?",
                            (analysis.scan.scan_id, limit)).fetchall()
        snapshot, snapshot_status = _parent_assertions(conn, analysis)
    return {"parent": dict(parent) if parent else None,
            "children": [dict(row) for row in rows], "total_children": total,
            "limit": limit, "snapshot": snapshot, "snapshot_status": snapshot_status}
