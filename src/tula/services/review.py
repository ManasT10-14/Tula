"""Audited human decisions; machine observations remain available in revision zero."""
from __future__ import annotations

from datetime import UTC, datetime

from ..domain.enums import DeclarationClass as DC
from ..domain.enums import ExtractionPath, Verdict
from ..domain.models import Declaration, ReviewDecision
from ..extract import normalizers
from ..extract.provenance import corrected_provenance, intelligence_record
from ..report.evidence import verify
from ..rules import exemptions
from .context import assessment_date, validate_context


def _reason(reason):
    if not isinstance(reason, str):
        raise ValueError("Provide a meaningful written reason.")  # noqa: TRY004 - review input uses domain validation errors.
    reason = reason.strip()
    characters = [c.casefold() for c in reason if c.isalnum()]
    if (not 5 <= len(reason) <= 3000 or len(characters) < 5
            or len(set(characters)) < 2 or not any(c.isalpha() for c in characters)):
        raise ValueError("Provide a meaningful written reason between 5 and 3,000 characters.")
    return reason


def _authorized_actor(actor):
    if (getattr(actor, "role", None) not in ("inspector", "supervisor", "admin")
            or getattr(actor, "active", True) is not True or not str(getattr(actor, "id", ""))):
        raise PermissionError("An active inspector, supervisor or administrator is required.")


def _editable(analysis, actor):
    _authorized_actor(actor)
    if actor.role not in ("supervisor", "admin") and analysis.scan.inspector_id != str(actor.id):
        raise PermissionError("Only the assigned inspector or a supervisor may change this inspection.")
    if analysis.review.status == "approved":
        raise ValueError("This inspection is approved. A supervisor must reopen it before changes.")


def _require_evidence(analysis, action):
    if not analysis.scan.frames:
        raise ValueError(f"No source images are retained. Evidence must verify before {action}.")
    try:
        verified = verify(analysis)
    except OSError as exc:
        raise ValueError(f"Evidence could not be read. Restore it before {action}.") from exc
    if not verified or any(row["status"] != "verified" for row in verified):
        raise ValueError(f"Evidence integrity could not be verified: source files are missing or changed. Restore them before {action}.")


MATERIAL_ACTIONS = frozenset({"finding.decided", "declaration.corrected", "package.context_confirmed",
                              "intelligence.corrected"})
MATERIAL_COMMENTS = frozenset({"context_confirmed", "intelligence_corrected"})


def _snapshot_contributors(analysis):
    actors = {str(d.actor_id) for d in analysis.review.decisions.values() if d.actor_id}
    actors.update(str(c["actor_id"]) for c in analysis.review.corrections if c.get("actor_id"))
    actors.update(str(c["actor_id"]) for c in analysis.review.comments
                  if c.get("action") in MATERIAL_COMMENTS and c.get("actor_id"))
    attested = analysis.package.legal_context.get("attested_by")
    if attested:
        actors.add(str(attested))
    return actors


def _material_contributors(repo, analysis):
    """Approval must remain independent after decisions are replaced/cleared.

    Read the retained history while revise() owns its write transaction. Plain
    comments, rescan requests and administrative reopenings are not material
    authorship and do not disqualify an otherwise independent supervisor.
    """
    actors = _snapshot_contributors(analysis)
    history = repo.revisions(analysis.scan.scan_id)
    if {item["revision"] for item in history} != set(range(analysis.review.revision + 1)):
        raise ValueError("Complete review history must be available before approval.")
    for item in history:
        if item["revision"] > analysis.review.revision:
            raise ValueError("Review history changed. Reload before approval.")
        if item["action"] in MATERIAL_ACTIONS and item.get("actor_id"):
            actors.add(str(item["actor_id"]))
        historic = repo.revision(analysis.scan.scan_id, item["revision"])
        if historic is None:
            raise ValueError("Complete review history must be available before approval.")
        if item["revision"] == 0 and historic.scan.inspector_id:
            actors.add(str(historic.scan.inspector_id))
        actors.update(_snapshot_contributors(historic))
    return actors


def _invalidate(review):
    review.status = "in_review"
    review.submitted_by = None
    review.approved_by = None
    review.approved_at = None
    review.approval_reason = ""


def decide(repo, scan_id, revision, actor, finding_id, verdict, reason):
    reason, verdict = _reason(reason), Verdict(verdict)
    if verdict not in (Verdict.PASS, Verdict.VIOLATION, Verdict.NOT_APPLICABLE, Verdict.INCONCLUSIVE):
        raise ValueError("Choose verified violation, satisfied, not applicable, or needs another scan.")

    def change(a):
        _editable(a, actor)
        if not any(f.finding_id == finding_id for f in a.findings):
            raise ValueError("This finding is no longer current. Reload the inspection.")
        _require_evidence(a, "recording a decision")
        a.review.decisions[finding_id] = ReviewDecision(
            finding_id=finding_id, verdict=verdict, reason=reason,
            actor_id=str(actor.id), actor_name=actor.display_name)
        _invalidate(a.review)
    return repo.revise(scan_id, revision, str(actor.id), "finding.decided", reason, change)


def correct(repo, rules, scan_id, revision, actor, kind, raw, frame_index, bbox, reason):
    reason, kind = _reason(reason), DC(kind)
    if not isinstance(raw, str):
        raise ValueError("Enter the verified label text.")  # noqa: TRY004 - review input uses domain validation errors.
    raw = raw.strip()
    if not raw or len(raw) > 4000:
        raise ValueError("Enter the label text, up to 4,000 characters.")
    if type(frame_index) is not int:
        raise ValueError("Choose a source image.")
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(type(value) is not int for value in bbox)):
        raise ValueError("Enter four whole-number pixel bounds for the evidence rectangle.")
    parsers = {DC.NET_QUANTITY: normalizers.parse_net_quantity,
               DC.RETAIL_SALE_PRICE: normalizers.parse_price,
               DC.UNIT_SALE_PRICE: normalizers.parse_unit_price,
               DC.DATE_OF_PACKING: normalizers.parse_date,
               DC.CONSUMER_CARE: normalizers.parse_contact,
               DC.MANUFACTURER: normalizers.parse_contact,
               DC.COUNTRY_OF_ORIGIN: normalizers.parse_country_of_origin}
    norm = parsers[kind](raw) if kind in parsers else {"name": raw, "present": True}
    if not norm:
        raise ValueError("This value cannot be normalized. Include its declaration cue, value and unit.")

    def change(a):
        _editable(a, actor)
        if rules.pack.version != a.rules_version:
            raise ValueError("Corrections require the exact original rule version.")
        if not 0 <= frame_index < len(a.scan.frames):
            raise ValueError("Choose a source image.")
        _require_evidence(a, "correcting a declaration")
        from PIL import Image
        frame = a.scan.frames[frame_index]
        with Image.open(frame) as im:
            if not bbox or len(bbox) != 4 or not (0 <= bbox[0] < bbox[2] <= im.width and
                                                0 <= bbox[1] < bbox[3] <= im.height):
                raise ValueError("Select a valid evidence rectangle inside the source image.")
            width, height = im.size
        before = a.declarations.get(kind)
        panel = next((d.get("panel", "unknown") for d in a.scan.capture_edits if d.get("frame") == frame),
                     next((d.get("panel", "unknown") for d in a.scan.image_diagnostics if d.get("frame") == frame),
                          before.panel if before and before.frame == frame else "unknown"))
        source = {"frame": frame, "panel": str(getattr(panel, "value", panel)), "bbox": list(bbox),
                  "text": raw, "image_index": frame_index, "image_width": width, "image_height": height,
                  "ocr_confidence": None, "method": "inspector_correction"}
        initial = repo.revision(scan_id, 0) if before and before.provenance is None else None
        provenance = corrected_provenance(before, source,
            original=initial.declarations.get(kind) if initial else None)
        updated = dict(norm, extraction_method="inspector_correction", verified_transcription=True, source_frame=frame,
                       corrected_by=str(actor.id), original_ocr=provenance.original_transcription,
                       original_source_spans=[s.model_dump(mode="json") for s in provenance.original_sources],
                       original_ocr_confidence=provenance.original_ocr_confidence,
                       source_spans=[source], extraction_confidence=None)
        a.declarations[kind] = Declaration(
            klass=kind, raw=raw, norm=updated, bbox=tuple(bbox), frame=frame, panel=panel,
            confidence=before.confidence if before else 0.0,
            scripts=normalizers.detect_scripts(raw), path=ExtractionPath.MANUAL,
            variants=[raw], provenance=provenance)
        a.intelligence.setdefault("declarations", {})[kind.value] = intelligence_record(a.declarations[kind])
        if kind.value in a.intelligence.get("fields", {}):
            a.intelligence["fields"][kind.value] = [{
                "value": raw, "raw": raw, "sources": [source], "candidates": [],
                "ocr_confidence": None,
                "extraction_confidence": None, "method": "inspector_correction", "status": "officer_corrected"}]
        a.review.corrections.append({"kind": kind.value, "before": before.model_dump(mode="json") if before else None,
                                    "after": a.declarations[kind].model_dump(mode="json"),
                                    "actor_id": str(actor.id), "actor": actor.display_name, "reason": reason,
                                    "created_at": datetime.now(UTC).isoformat()})
        if a.package.legal_context.get("imported_confirmed") is True:
            a.package.is_imported = a.package.legal_context["is_imported"]
        else:
            a.package.is_imported = any(normalizers.looks_imported(d.raw) for d in a.declarations.values())
        nq = a.declarations.get(DC.NET_QUANTITY)
        a.package.capacity_value = nq.norm.get("value_base") if nq else None
        a.package.capacity_unit = nq.norm.get("unit_base") if nq else None
        if kind == DC.DATE_OF_PACKING:
            from datetime import date
            a.scan.packing_date = date(int(norm["year"]), int(norm.get("month") or 1), 1)
            if a.scan.packing_date > datetime.now(UTC).date():
                raise ValueError("Packing date is in the future. Record the ambiguity in a comment and rescan.")
        determination = exemptions.determine(
            a.declarations, raw_text="\n".join(d.raw for d in a.declarations.values()),
            legal_context=a.package.legal_context,
            as_of=assessment_date(a.package.legal_context, a.scan.packing_date),
            policy=getattr(rules.pack, "legal_policy", {}))
        a.package = exemptions.apply(a.package, determination)
        # A changed box/text cannot inherit an earlier typography measurement.
        a.measurements.pop("min_declaration_height", None)
        if kind == DC.NET_QUANTITY:
            a.measurements.pop("net_quantity_cap_height", None)
        forensic = [f.model_copy(update={"verdict": Verdict.INCONCLUSIVE,
                                        "message": "Forensic context needs review after a declaration correction",
                                        "detail": "A declaration changed. Verify the barcode and its context against the original image."})
                    for f in a.findings if f.rule_id.startswith("FORENSIC.")]
        a.findings = rules.evaluate_all(a.scan, a.package, a.declarations, a.measurements) + forensic
        a.review.decisions.clear()
        _invalidate(a.review)
    return repo.revise(scan_id, revision, str(actor.id), "declaration.corrected", reason, change)


def confirm_context(repo, rules, scan_id, revision, actor, context, reason):
    """An officer attestation changes applicability in a new, auditable revision."""
    reason = _reason(reason)

    def change(a):
        _editable(a, actor)
        if rules.pack.version != a.rules_version:
            raise ValueError("Context review requires the original rule version.")
        _require_evidence(a, "confirming package facts")
        from zoneinfo import ZoneInfo
        assessed_on = assessment_date(a.package.legal_context,
                                      a.scan.captured_at.astimezone(ZoneInfo("Asia/Kolkata")).date())
        before = dict(a.package.legal_context)
        a.package.legal_context = validate_context(context, actor_id=str(actor.id), assessed_on=assessed_on)
        if before.get("scope_evidence_text"):
            a.package.legal_context["scope_evidence_text"] = before["scope_evidence_text"]
        if a.package.legal_context.get("imported_confirmed") is True:
            a.package.is_imported = a.package.legal_context["is_imported"]
        else:
            a.package.is_imported = any(normalizers.looks_imported(d.raw) for d in a.declarations.values())
        determination = exemptions.determine(a.declarations,
            raw_text="\n".join(d.raw for d in a.declarations.values()),
            legal_context=a.package.legal_context, as_of=assessed_on,
            policy=getattr(rules.pack, "legal_policy", {}))
        a.package = exemptions.apply(a.package, determination)
        forensic = [f.model_copy(update={"verdict": Verdict.INCONCLUSIVE,
            "message": "Verify identifier context after the package facts changed"})
            for f in a.findings if f.rule_id.startswith("FORENSIC.")]
        a.findings = rules.evaluate_all(a.scan, a.package, a.declarations, a.measurements) + forensic
        a.review.decisions.clear()
        a.review.comments.append({"actor_id": str(actor.id), "actor": actor.display_name,
            "action": "context_confirmed", "text": reason, "before": before,
            "after": dict(a.package.legal_context), "created_at": datetime.now(UTC).isoformat()})
        _invalidate(a.review)

    return repo.revise(scan_id, revision, str(actor.id), "package.context_confirmed", reason, change)


def transition(repo, scan_id, revision, actor, action, reason):
    reason = _reason(reason)

    def change(a):
        _authorized_actor(actor)
        if action == "reopen":
            if actor.role not in ("supervisor", "admin"):
                raise PermissionError("A supervisor must reopen an approved inspection.")
            if a.review.status != "approved":
                raise ValueError("Only an approved inspection can be reopened.")
            _invalidate(a.review)
        elif action == "approve":
            if actor.role not in ("supervisor", "admin"):
                raise PermissionError("Supervisor approval is required.")
            if str(actor.id) in (a.scan.inspector_id, a.review.submitted_by):
                raise PermissionError("A different supervisor must approve this inspection.")
            if str(actor.id) in _material_contributors(repo, a):
                raise PermissionError("Approval must come from a supervisor who did not correct or decide this inspection, including earlier package-fact or label edits.")
            if a.review.status != "submitted" or a.pending_review or not a.findings:
                raise ValueError("Resolve pending findings and submit the inspection before approval.")
            _require_evidence(a, "approval")
            a.review.status = "approved"
            a.review.approved_by = actor.display_name
            a.review.approved_at = datetime.now(UTC)
            a.review.approval_reason = reason
        elif action == "submit":
            _editable(a, actor)
            if a.review.status not in ("draft", "in_review"):
                raise ValueError("This inspection is already submitted. Reload its current workflow state.")
            if a.pending_review or not a.findings:
                raise ValueError("Resolve each potential violation and uncertain finding before submitting.")
            _require_evidence(a, "submission")
            a.review.status = "submitted"
            a.review.submitted_by = str(actor.id)
        elif action in ("comment", "request_rescan"):
            _editable(a, actor)
            a.review.comments.append({"actor_id": str(actor.id), "actor": actor.display_name,
                                      "action": action, "text": reason,
                                      "created_at": datetime.now(UTC).isoformat()})
            if action == "request_rescan":
                _invalidate(a.review)
        else:
            raise ValueError("Unknown review action.")
    return repo.revise(scan_id, revision, str(actor.id), "inspection."+action, reason, change)
