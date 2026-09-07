"""Generate evidence-checked exports and retain the artifact and audit atomically."""
from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..domain.models import Analysis, sha256_file
from ..observability import log_event
from ..report import docx_export, pdf
from ..report.evidence import verify


class ReportError(ValueError):
    """An export cannot be supported by its retained evidence."""


def _require_evidence(analysis: Analysis) -> None:
    if not analysis.scan.frames or any(row["status"] != "verified" for row in verify(analysis)):
        raise ReportError("Evidence is missing or changed. Export the JSON record and investigate before generating a report.")


def export_inspection(repo, security, actor, analysis: Analysis, output_dir: Path,
                      fmt: str, *, premises: str = "") -> Path:
    if fmt not in {"pdf", "docx", "notice.docx"}:
        raise ValueError("Unknown report format.")
    if repo.path.resolve() != security.path.resolve():
        raise ReportError("Report and audit storage must use the same inspection database.")
    _require_evidence(analysis)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    identity = re.sub(r"[^A-Za-z0-9_-]", "_", analysis.scan.scan_id)[:80] or "inspection"
    suffix = "notice.docx" if fmt == "notice.docx" else f"report.{fmt}"
    path = destination / f"{identity}-{uuid.uuid4().hex}-{suffix}"
    started = time.perf_counter()
    logger = logging.getLogger("tula.reports")
    log_event(
        logger,
        logging.INFO,
        "report_generation_started",
        scan_id=analysis.scan.scan_id,
        format=fmt,
        revision=analysis.review.revision,
    )
    try:
        if fmt == "pdf":
            pdf.write(analysis, path, json_sidecar=False)
        elif fmt == "docx":
            docx_export.write(analysis, path)
        else:
            docx_export.write_notice(analysis, path, premises=premises)
        # Evidence may have changed during a long render; do not publish that export.
        _require_evidence(analysis)
        digest = sha256_file(str(path))
        artifact_id = uuid.uuid4().hex
        with repo._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO report_artifact VALUES (?,?,?,?,?,?,?,?)", (
                artifact_id, analysis.scan.scan_id, analysis.review.revision, fmt, str(path),
                digest, actor.id, datetime.now(UTC).isoformat()))
            security._audit(conn, actor_id=actor.id, action="report.generated",
                entity_type="inspection", entity_id=analysis.scan.scan_id,
                after={"artifact_id": artifact_id, "format": fmt,
                       "revision": analysis.review.revision, "sha256": digest})
        log_event(
            logger,
            logging.INFO,
            "report_generation_completed",
            scan_id=analysis.scan.scan_id,
            format=fmt,
            revision=analysis.review.revision,
            artifact_id=artifact_id,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return path
    except BaseException as exc:
        # Only this new, uniquely named artifact is removed. Retained exports are untouched.
        path.unlink(missing_ok=True)
        log_event(
            logger,
            logging.ERROR,
            "report_generation_failed",
            scan_id=analysis.scan.scan_id,
            format=fmt,
            revision=analysis.review.revision,
            error_type=type(exc).__name__,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
