"""Immutable parent receipts and evidence checks for same-package close-ups.

The receipt keeps prior officer assertions available for comparison. It never
promotes those assertions, decisions or approvals into the new OCR result.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain.enums import DeclarationClass
from ..domain.models import Analysis, sha256_file
from ..report.evidence import verify
from ..rules.spec import RulePack
from .capture import CaptureError

TARGET_LABELS = {
    **{kind.value: kind.value.replace("_", " ").capitalize() for kind in DeclarationClass},
    "manufacturing_date": "Manufacturing date", "packing_date": "Packing date", "expiry_date": "Expiry date",
    "best_before": "Best before", "use_by": "Use by", "batch_number": "Batch / lot number",
    "ingredients": "Ingredients",
}


class RescanError(CaptureError):
    def __init__(self, message, status=409):
        super().__init__(message, status)


def validate_target(target, parent):
    if not isinstance(target, str) or (target and target not in TARGET_LABELS):
        raise RescanError("Choose a supported declaration for the close-up.", 422)
    if target and not parent:
        raise RescanError("A targeted close-up must link to its original inspection.", 422)
    return target


def current_parent(conn, scan_id, actor, revision=None):
    """Authorize and check the displayed revision before accepting more evidence."""
    if revision is not None and (type(revision) is not int or revision < 0):
        raise RescanError("Provide a valid original inspection revision.", 422)
    row = conn.execute("SELECT record FROM inspection WHERE scan_id=?", (scan_id,)).fetchone()
    if row is None:
        raise RescanError("The original inspection could not be found.", 404)
    parent = Analysis.model_validate_json(row["record"])
    if (getattr(actor, "active", True) is not True
            or actor.role not in {"inspector", "supervisor", "admin"}
            or (parent.scan.inspector_id != str(actor.id) and actor.role not in {"admin", "supervisor"})):
        raise RescanError("Only the assigned inspector or supervisor may add a rescan.", 403)
    if revision is not None and parent.review.revision != revision:
        raise RescanError("The original inspection changed. Reload it before adding a close-up.")
    try:
        checked = verify(parent)
        if not parent.scan.frames or not checked or any(r["status"] != "verified" for r in checked):
            raise RescanError("The original evidence is missing or changed. Restore it before adding a rescan.")
    except OSError:
        raise RescanError("The original evidence is missing or changed. Restore it before adding a rescan.") from None
    return parent


def snapshot_parent(conn, scan_id, actor, revision=None):
    parent = current_parent(conn, scan_id, actor, revision)
    row = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=?",
                       (scan_id, parent.review.revision)).fetchone()
    if row is None or Analysis.model_validate_json(row["record"]) != parent:
        raise RescanError("The original revision history is unavailable or inconsistent.")
    rule = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (parent.rules_version,)).fetchone()
    if (rule is None or hashlib.sha256(rule["record"].encode()).hexdigest() != rule["sha256"]
            or RulePack.model_validate_json(rule["record"]).version != parent.rules_version):
        raise RescanError("The original rule archive is missing or changed. Restore it before adding a rescan.")
    return parent, {"scan_id": scan_id, "revision": parent.review.revision,
                    "record": row["record"], "record_sha256": hashlib.sha256(row["record"].encode()).hexdigest(),
                    "rules_version": parent.rules_version}


def _digest(path, *, optional=False):
    source = Path(path)
    try:
        if optional and not source.exists() and not source.is_symlink():
            return None
        if source.is_symlink() or not source.is_file():
            raise OSError("Missing or linked evidence")
        return sha256_file(str(source))
    except OSError:
        raise RescanError("Queued evidence is missing or changed. Restore the original files before retrying.") from None


def evidence_manifest(captures, originals, *, parent=None):
    """Hash current bytes, comparing retained hashes rather than rebasing them."""
    expected = dict(originals)
    if parent:
        for frame in parent.scan.frames:
            digest = parent.scan.frame_hashes.get(frame) or parent.scan.frame_hashes.get(Path(frame).name)
            if not digest:
                raise RescanError("The original inspection has no retained working-image hash.")
            expected[frame] = digest
        if any(originals.get(path) != digest for path, digest in parent.scan.original_frame_hashes.items()):
            raise RescanError("The original source-image manifest changed.")
    files = {}
    for capture in captures:
        digest = _digest(capture.path)
        if capture.path in expected and expected[capture.path] != digest:
            raise RescanError("Queued evidence is missing or changed. Restore the original files before retrying.")
        files[capture.path] = {"path": capture.path, "sha256": digest, "kind": "working"}
        metadata = str(Path(capture.path).with_suffix(".meta.json"))
        files[metadata] = {"path": metadata, "sha256": _digest(metadata, optional=True), "kind": "metadata"}
    for path, digest in originals.items():
        if not digest or _digest(path) != digest:
            raise RescanError("Queued original evidence is missing or changed. Restore it before retrying.")
        if path not in files:
            files[path] = {"path": path, "sha256": digest, "kind": "original"}
    return list(files.values())


def validate_job(conn, payload, *, actor=None):
    """Recheck an anchored job; subsequent parent review revisions are allowed."""
    parent = None
    if payload.get("parent"):
        receipt = payload.get("parent_snapshot")
        if not isinstance(receipt, dict):
            raise RescanError("This older rescan has no captured parent revision. Start a new linked close-up.")
        try:
            record = receipt["record"]
            row = conn.execute("SELECT record FROM inspection_revision WHERE scan_id=? AND revision=?",
                               (payload["parent"], receipt["revision"])).fetchone()
            if (row is None or row["record"] != record
                    or hashlib.sha256(record.encode()).hexdigest() != receipt["record_sha256"]):
                raise ValueError("Parent receipt mismatch")
            parent = Analysis.model_validate_json(record)
            if (parent.scan.scan_id != payload["parent"] or receipt["scan_id"] != payload["parent"]
                    or parent.review.revision != receipt["revision"]
                    or parent.rules_version != payload["rules_version"]
                    or receipt["rules_version"] != parent.rules_version):
                raise ValueError("Parent identity mismatch")
            if [c["path"] for c in payload["captures"][:len(parent.scan.frames)]] != parent.scan.frames:
                raise ValueError("Parent evidence omitted")
        except (KeyError, TypeError, ValueError):
            raise RescanError("The captured original revision is missing or changed. Restore its retained history.") from None
        if actor is not None:
            # Authorization is checked against current assignment; facts remain
            # anchored to the earlier captured revision.
            current_parent(conn, payload["parent"], actor)
        rule = conn.execute("SELECT record,sha256 FROM rule_version WHERE version=?", (parent.rules_version,)).fetchone()
        if (rule is None or hashlib.sha256(rule["record"].encode()).hexdigest() != rule["sha256"]
                or RulePack.model_validate_json(rule["record"]).version != parent.rules_version):
            raise RescanError("The original rule archive is missing or changed. Restore it before retrying.")
    manifest = payload.get("expected_files")
    if manifest is None:
        if parent:
            raise RescanError("This rescan has no retained evidence manifest. Start a new linked close-up.")
        return parent  # Historical ordinary jobs remain readable and retryable.
    expected = {entry["path"]: entry["sha256"] for entry in manifest}
    for capture in payload["captures"]:
        if capture["path"] not in expected or str(Path(capture["path"]).with_suffix(".meta.json")) not in expected:
            raise RescanError("The queued working-image manifest is incomplete.")
    for path, digest in payload["hashes"].items():
        if expected.get(path) != digest:
            raise RescanError("The queued original-image manifest is inconsistent.")
    if parent:
        for frame in parent.scan.frames:
            digest = parent.scan.frame_hashes.get(frame) or parent.scan.frame_hashes.get(Path(frame).name)
            if expected.get(frame) != digest:
                raise RescanError("The queued parent-image manifest is inconsistent.")
        if any(payload["hashes"].get(path) != digest for path, digest in parent.scan.original_frame_hashes.items()):
            raise RescanError("The queued parent-original manifest is inconsistent.")
    for entry in manifest:
        if _digest(entry["path"], optional=entry["kind"] == "metadata") != entry["sha256"]:
            raise RescanError("Queued evidence or capture metadata is missing or changed. Restore it before retrying.")
    return parent


def retained_date_context(context, parent):
    """Keep the earlier legal date basis, without copying earlier attestations."""
    result = dict(context)
    for key in ("assessment_date", "assessment_date_confirmed"):
        result.pop(key, None)
        if key in parent.package.legal_context:
            result[key] = parent.package.legal_context[key]
    return result
