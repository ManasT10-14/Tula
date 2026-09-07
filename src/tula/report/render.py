"""One view model, three renderers.

The PDF, the DOCX and the JSON record must never disagree about what was found,
so all three are built from this. Section order follows PRD section 13, and the
applicability section comes third on purpose: it pre-empts the first defence any
respondent raises, which is that the rule never applied to their package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.enums import DeclarationClass, Verdict
from ..domain.models import Analysis, Finding
from ..extract.provenance import get_provenance

VERDICT_LABEL = {
    Verdict.PASS: "Check passed",
    Verdict.VIOLATION: "Potential violation",
    Verdict.ADVISORY: "Advisory",
    Verdict.INCONCLUSIVE: "Inconclusive",
    Verdict.NOT_APPLICABLE: "Not applicable",
    Verdict.EXEMPT: "Exempt",
    Verdict.UNVERIFIED: "Unverified",
}

VERDICT_RGB = {
    Verdict.PASS: (0x18, 0x65, 0x40),
    Verdict.VIOLATION: (0xA3, 0x1D, 0x14),
    Verdict.ADVISORY: (0x84, 0x59, 0x0A),
    Verdict.INCONCLUSIVE: (0x84, 0x59, 0x0A),
    Verdict.NOT_APPLICABLE: (0x56, 0x63, 0x5F),
    Verdict.EXEMPT: (0x0A, 0x66, 0x69),
    Verdict.UNVERIFIED: (0x84, 0x59, 0x0A),
}

DECLARATION_LABEL = {
    DeclarationClass.MANUFACTURER: "Manufacturer / packer / importer",
    DeclarationClass.GENERIC_NAME: "Common or generic name",
    DeclarationClass.NET_QUANTITY: "Net quantity",
    DeclarationClass.DATE_OF_PACKING: "Month and year of packing",
    DeclarationClass.RETAIL_SALE_PRICE: "Retail sale price",
    DeclarationClass.CONSUMER_CARE: "Consumer care details",
    DeclarationClass.UNIT_SALE_PRICE: "Unit sale price",
    DeclarationClass.COUNTRY_OF_ORIGIN: "Country of origin",
    DeclarationClass.BRAND: "Brand (not a mandatory declaration)",
}


def finding_message(finding: Finding) -> str:
    """Present an unresolved check without asserting its stored failure wording."""
    subject = DECLARATION_LABEL.get(finding.declaration, finding.citation.clause or "This rule check")
    if finding.verdict == Verdict.INCONCLUSIVE:
        return f"{subject}: the available evidence is insufficient for a machine conclusion."
    if finding.verdict == Verdict.UNVERIFIED:
        return f"{subject}: this machine check requires further verification."
    return finding.message


@dataclass
class Section:
    title: str
    kind: str  # "kv" | "table" | "text" | "findings"
    rows: list[Any] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    note: str = ""
    evidence: list[dict] = field(default_factory=list)


def declaration_view(declaration, analysis):
    """One primary-field evidence view for HTML and document renderers."""
    provenance = get_provenance(declaration)
    manual = provenance.method == "inspector_correction"
    if manual:
        scores = "Officer-verified transcription; no automated OCR or extraction score is assigned to the corrected text."
    else:
        scores = (f"OCR model score: {provenance.ocr_confidence:.2f} (not calibrated)."
                  if provenance.ocr_confidence is not None else "OCR model score: not recorded.")
        scores += (f" Extraction score: {provenance.extraction_confidence:.2f} (heuristic, not an accuracy probability)."
                   if provenance.extraction_confidence is not None else " Extraction score: not available; verify the unresolved or historical reading.")
    if provenance.original_ocr_confidence is not None:
        scores += f" Original OCR model score: {provenance.original_ocr_confidence:.2f} (not calibrated)."

    def located(source):
        index = analysis.scan.frames.index(source.frame) if source.frame in analysis.scan.frames else None
        if index is None and source.frame is None and source.image_index is not None and 0 <= source.image_index < len(analysis.scan.frames):
            index = source.image_index
        reference = f"Image {index + 1}" if index is not None else "Source image not resolved"
        reference += f"; panel {source.panel.value}; pixels {list(source.bbox) if source.bbox else 'not located'}"
        if source.ocr_confidence is not None:
            reference += f"; source OCR score {source.ocr_confidence:.2f}"
        return {**source.model_dump(mode="json"), "image_index": index, "reference": reference}

    return {"raw": declaration.raw, "method": provenance.method.replace("_", " "),
            "status": provenance.status.replace("_", " "), "scores": scores,
            "score_basis": provenance.score_basis, "sources": [located(source) for source in provenance.sources],
            "manual": manual, "original_transcription": provenance.original_transcription,
            "original_sources": [located(source) for source in provenance.original_sources]}


def declaration_details(view):
    parts = [f"Method: {view['method']}; status: {view['status']}.", view["scores"]]
    if view["score_basis"] not in {"not_recorded", "human_transcription_no_machine_score"}:
        parts.append("Score basis: " + view["score_basis"])
    parts.extend(source["reference"] + "; source text: " + source["text"] for source in view["sources"])
    if not view["sources"]:
        parts.append("No source location recorded; manual verification required.")
    if view["original_transcription"] is not None:
        parts.append("Original OCR transcription: " + view["original_transcription"])
        parts.extend("Original evidence: " + source["reference"] for source in view["original_sources"])
    return "\n".join(parts)


def headline(analysis: Analysis) -> str:
    """The one sentence that goes at the top of the report."""
    verified = len(analysis.verified_violations)
    potential = len(analysis.violations)
    if analysis.review.status == "approved":
        if verified:
            return f"Supervisor review approved: {verified} inspector-verified violation(s)."
        return "Supervisor review approved: no verified violation in the requirements examined."
    return (f"Review required: {potential} machine-flagged potential violation(s), "
            f"{verified} inspector-verified violation(s), and {len(analysis.pending_review)} unresolved finding(s).")


def review_banner(analysis: Analysis) -> str:
    if analysis.review.status == "approved":
        return "SUPERVISOR REVIEW APPROVED - DRAFT LEGAL RULE PACK"
    if analysis.review.status == "submitted":
        return "INSPECTOR REVIEW SUBMITTED - SUPERVISOR APPROVAL REQUIRED"
    return "INSPECTION UNDER REVIEW - FINDINGS REQUIRE VERIFICATION"


def scope_note(analysis: Analysis) -> str:
    return (f"This report records inspection {analysis.scan.scan_id}, revision {analysis.review.revision}. "
            "It distinguishes automated screening from recorded officer decisions. "
            "The legal rules pack remains a draft; recorded workflow approval is not statutory certification "
            "or authorization to serve a notice. Changes made in an editable export do not update the inspection record."
            + (f" This is a linked rescan of {analysis.scan.parent_scan_id}; the earlier inspection and its "
               "officer decisions remain retained separately. Approval of either record does not approve the other."
               if analysis.scan.parent_scan_id else ""))


def _location(scan) -> str:
    parts = [scan.region.strip()] if scan.region.strip() else []
    if scan.geo:
        parts.append(f"GPS {scan.geo[0]:.5f}, {scan.geo[1]:.5f}")
    return "; ".join(parts) or "not recorded"


def _confirmed_context(package) -> list[tuple[str, str]]:
    context = package.legal_context
    imported = context.get("is_imported")
    origin = "Not confirmed"
    if context.get("imported_confirmed") is True and isinstance(imported, bool):
        origin = ("Yes" if imported else "No") + " (officer-confirmed)"
    category = context.get("category")
    known_categories = {"general", "food", "alcohol", "tobacco", "pan_masala", "medical_device", "cosmetic", "seed"}
    category_text = "Not confirmed"
    if context.get("category_confirmed") is True and isinstance(category, str) and category in known_categories:
        category_text = category.replace("_", " ").capitalize() + " (officer-confirmed)"
    rows = [("Imported commodity", origin), ("Product category", category_text)]
    if context.get("attested_by") and (origin != "Not confirmed" or category_text != "Not confirmed"):
        rows.append(("Context attested by account", str(context["attested_by"])))
    return rows


def build(analysis: Analysis) -> list[Section]:
    scan, package = analysis.scan, analysis.package
    sections: list[Section] = []

    # ---- 1. cover -------------------------------------------------------
    sections.append(
        Section(
            "Inspection particulars",
            "kv",
            [
                ("Inspection reference", scan.scan_id),
                ("Captured", scan.captured_at.strftime("%d %B %Y, %H:%M UTC")),
                ("Lane", scan.lane.value),
                ("Capture source", scan.source),
                ("Officer", scan.operator or "not recorded"),
                ("Location", _location(scan)),
                ("Rules version applied", analysis.rules_version),
                ("Date of packing declared", str(scan.packing_date) if scan.packing_date else "not declared"),
                ("Evidence assurance tier", f"Tier {scan.tier.value}"),
                ("Recognition engine", analysis.engine),
                ("Machine screening outcome", VERDICT_LABEL[analysis.overall]),
                ("Record revision", str(analysis.review.revision)),
            ],
        )
    )

    if scan.parent_scan_id:
        sections.append(Section("Original inspection reference", "kv", [
            ("Original inspection", scan.parent_scan_id),
            ("Original revision used", str(scan.parent_revision) if scan.parent_revision is not None else "Not recorded in this historical record"),
            ("Original record SHA-256", scan.parent_record_sha256 or "Not recorded"),
            ("Close-up focus", scan.rescan_target.replace("_", " ").capitalize() or "Additional package evidence"),
        ], note="Earlier officer corrections and approval remain in the referenced original revision. They have not been applied automatically to this new analysis."))

    sections.append(Section("Review summary", "kv", [
        ("Inspection status", analysis.product_status.replace("_", " ")),
        ("Workflow state", analysis.review.status.replace("_", " ")),
        ("Machine potential violations", str(len(analysis.violations))),
        ("Inspector verified violations", str(len(analysis.verified_violations))),
        ("Findings awaiting resolution", str(len(analysis.pending_review))),
        ("Supervisor approval", analysis.review.approved_by or "Not recorded"),
    ], note=scope_note(analysis)))

    # ---- 2. product identification --------------------------------------
    rows, declaration_evidence = [], []
    for klass, decl in analysis.declarations.items():
        view = declaration_view(decl, analysis)
        declaration_evidence.append(view)
        rows.append(
            (
                DECLARATION_LABEL.get(klass, klass.value),
                decl.raw + "\n" + declaration_details(view),
                decl.panel.value,
                ", ".join(s.value for s in decl.scripts) or "-",
            )
        )
    sections.append(
        Section(
            "Declarations extracted",
            "table",
            rows,
            columns=["Declaration", "As printed", "Panel", "Scripts"],
            evidence=declaration_evidence,
            note=(
                f"GTIN: {package.gtin}" if package.gtin else "No valid GTIN was decoded."
            ),
        )
    )

    # ---- 3. applicability -----------------------------------------------
    applicability = [
        ("Package class", package.klass.value),
        (
            "Exemptions applied",
            "; ".join(package.exemptions) if package.exemptions else "None",
        ),
        *_confirmed_context(package),
        (
            "Panels captured",
            ", ".join(sorted({p.value for p in package.panels_captured})) or "none",
        ),
    ]
    sections.append(
        Section(
            "Applicability and exemptions",
            "kv",
            applicability,
            note=(
                "Rule 3 and Rule 26 were considered before any declaration was tested. "
                "Where a package is exempt, the corresponding rules are recorded as exempt "
                "rather than as violations."
            ),
        )
    )

    # ---- 4. findings -----------------------------------------------------
    sections.append(
        Section(
            "Findings",
            "findings",
            list(analysis.findings),
            note=headline(analysis),
        )
    )

    # ---- 5. measurement annexe -------------------------------------------
    measure_rows = []
    for key, m in analysis.measurements.items():
        measure_rows.append(
            (key, f"{m.value:.2f} {m.unit}", f"± {m.uncertainty:.2f}", f"Tier {m.tier.value}", m.method or "-")
        )
    if package.pdp_area_cm2:
        m = package.pdp_area_cm2
        measure_rows.append(
            ("pdp_area", f"{m.value:.1f} {m.unit}", f"± {m.uncertainty:.1f}",
             f"Tier {m.tier.value}", m.method or "-")
        )
    scale_note = "; ".join(
        f"{s.source} = {s.mm_per_px:.5f} mm/px (±{s.sigma:.5f}, Tier {s.tier.value})"
        for s in scan.scales
    ) or "No scale reference was available."
    sections.append(
        Section(
            "Measurement annexe",
            "table",
            measure_rows,
            columns=["Quantity", "Value", "Uncertainty (k=2)", "Tier", "Method"],
            note=(
                "Scale sources: " + scale_note + ". Uncertainties use a coverage factor of k=2; "
                "coverage has not been established by independent instrument calibration. "
                "A potential typography issue requires the measurement interval to lie beyond "
                "the draft pack threshold and remains subject to officer verification."
            ),
        )
    )

    # ---- 6. adjudication trail -------------------------------------------
    sections.append(
        Section(
            "Adjudication trail",
            "kv",
            [
                ("Machine determination", VERDICT_LABEL[analysis.overall]),
                ("Reviewing officers", ", ".join(sorted({d.actor_name for d in analysis.review.decisions.values()})) or "No decisions recorded"),
                ("Recorded finding decisions", str(len(analysis.review.decisions))),
                ("Submitted by account", analysis.review.submitted_by or "Not submitted"),
                ("Approved by", analysis.review.approved_by or "Not approved"),
                ("Approved at", analysis.review.approved_at.strftime("%d %B %Y, %H:%M UTC") if analysis.review.approved_at else "Not recorded"),
                ("Approval reason", analysis.review.approval_reason or "Not recorded"),
                ("Record revision", str(analysis.review.revision)),
                ("Analysis time", f"{analysis.elapsed_ms} ms"),
            ],
            note=(
                "Individual officer decisions accompany each finding. Workflow approval is recorded "
                "separately from the original machine output. This export does not contain a digital signature."
            ),
        )
    )

    if analysis.review.corrections:
        sections.append(Section("Declaration corrections", "table", [
            (str(c.get("kind", "")).replace("_", " "), _correction_value(c.get("before")),
             _correction_value(c.get("after")),
             f"{c.get('actor', c.get('actor_id', 'Not recorded'))}\n{c.get('created_at', '')}\nReason: {c.get('reason', '')}")
            for c in analysis.review.corrections
        ], columns=["Declaration", "Before correction", "After correction", "Recorded by and reason"],
        note="Original OCR evidence remains preserved. Corrected values are officer assertions linked to recorded evidence."))
    if analysis.review.comments:
        sections.append(Section("Officer notes and rescan requests", "text", [
            f"{c.get('actor', c.get('actor_id', 'Not recorded'))} | {c.get('created_at', '')} | "
            f"{str(c.get('action', 'comment')).replace('_', ' ')}: {c.get('text', '')}"
            for c in analysis.review.comments]))
    sections.extend(_intelligence_sections(analysis))

    # ---- 7. evidence integrity -------------------------------------------
    from .evidence import verify
    evidence_rows = [(Path(r['frame']).name, r['expected'] or "not hashed", r['status'])
                     for r in verify(analysis)]
    sections.append(
        Section(
            "Evidence integrity",
            "table",
            evidence_rows,
            columns=["Frame", "SHA-256", "Status now"],
            note=(
                "Frames are hashed before analysis; original uploads are also retained. "
                "This establishes integrity from ingestion, not signed camera-time custody."
            ),
        )
    )

    if analysis.warnings:
        sections.append(
            Section("Notes and limitations", "text", list(analysis.warnings))
        )

    return sections


def finding_rows(finding: Finding, analysis: Analysis | None = None) -> list[tuple[str, str]]:
    """Key/value detail lines under one finding."""
    rows: list[tuple[str, str]] = [("Rule", f"{finding.citation.clause} ({finding.rule_id})")]
    if finding.citation.text:
        rows.append(("Rule text / screening basis", finding.citation.text))
    if finding.extracted:
        rows.append(("Observed declaration", finding.extracted.replace("\n", "  /  ")))
    if finding.measured:
        rows.append(("Measured", finding.measured.render()))
        rows.append(("Method", finding.measured.method or "-"))
    if finding.threshold is not None:
        rows.append(("Prescribed minimum", f"{finding.threshold:.2f} mm"))
    if finding.threshold_basis:
        rows.append(("Threshold basis", finding.threshold_basis))
    if finding.detail:
        rows.append(("Reasoning", finding.detail))
    if finding.penalty_ref:
        rows.append(("Penalty reference", finding.penalty_ref))
    rows.append(("Rules version", finding.rules_version))
    rows.append(("Machine result", VERDICT_LABEL[finding.verdict]))
    rows.append(("Severity", finding.severity.value))
    rows.append(("Rule evaluation", "Deterministic screening logic" if not finding.rule_id.startswith("FORENSIC.") else "Forensic screening signal"))
    if analysis:
        declaration = analysis.declarations.get(finding.declaration)
        if declaration:
            source = next((i + 1 for i, frame in enumerate(analysis.scan.frames) if frame == declaration.frame), None)
            view = declaration_view(declaration, analysis)
            rows.append(("Evidence reference", "; ".join(s["reference"] for s in view["sources"]) or f"Image {source or 'unresolved'}; source not located"))
            rows.append(("Extraction provenance", f"{view['method']}; {view['scores']}"))
        decision = analysis.review.decisions.get(finding.finding_id)
        if decision:
            label = "Verified violation" if decision.verdict is Verdict.VIOLATION else decision.verdict.value.replace("_", " ")
            rows.extend([("Inspector decision", label), ("Decision recorded by", decision.actor_name),
                         ("Decision time", decision.created_at.strftime("%d %B %Y, %H:%M UTC")),
                         ("Officer reason", decision.reason)])
        else:
            rows.append(("Inspector decision", "Not recorded - machine result remains unverified"))
    return rows


def _correction_value(value) -> str:
    if not value:
        return "No declaration recorded"
    if not isinstance(value, dict):
        return str(value)
    raw = value.get("raw", "")
    normalized = value.get("norm", {})
    keys = ("value", "unit", "currency", "iso", "country", "name")
    normalized_text = "; ".join(f"{key}: {normalized[key]}" for key in keys if key in normalized)
    return str(raw) + ("\nNormalized: " + normalized_text if normalized_text else "")


def _intelligence_sections(analysis: Analysis) -> list[Section]:
    data = analysis.intelligence
    if not data:
        return []
    rows = []
    for name, observations in data.get("fields", {}).items():
        if name in {"net_quantity", "retail_sale_price", "unit_sale_price"}:
            continue
        for item in observations:
            value = item.get("value")
            printed = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value or item.get("raw", "Unresolved"))
            candidates = [c.get("iso") or c.get("raw", "") for c in item.get("candidates", [])]
            status = str(item.get("status", "needs_review")).replace("_", " ")
            if candidates:
                status += "; candidates: " + ", ".join(candidates)
            if item.get("warning"):
                status += "; " + item["warning"]
            rows.append((name.replace("_", " "), printed, status))
    sections = [Section("Supplementary label information", "table", rows,
                        columns=["Field", "Printed value or statement", "Interpretation"],
                        note="Dates and ingredients are informational observations. Ambiguous dates retain competing interpretations; no expiry date is inferred from a relative duration.")]
    observations = []
    product = data.get("product", {})
    for label, key, evidence_key in (("Suggested category", "category", "category_evidence"),
                                     ("Origin signal", "origin", "origin_evidence")):
        value = product.get(key)
        items = product.get(evidence_key, [])
        if value and (value != "unknown" or items):
            detail = "Officer confirmation required. " + ("\n\n".join(
                _observation_details(item, analysis) for item in items
            ) or "No supporting text was located.")
            observations.append((label, str(value).replace("_", " "), detail))
    for item in product.get("package_type_evidence", []):
        observations.append(("Printed package reference", str(item.get("value") or item.get("raw", "")),
                             "Verify the physical package. " + _observation_details(item, analysis)))
    if observations:
        sections.append(Section("Product context observations", "table", observations,
                                columns=["Observation", "Value or printed statement", "Evidence and uncertainty"],
                                note="Text-based suggestions do not establish legal exemptions or certify contents. Confirmed context is recorded separately under applicability; package references are not visual material classification."))
    claims = []
    for item in data.get("dietary", []):
        claims.append(("Printed dietary claim", str(item.get("raw") or item.get("value", "")),
                       _observation_details(item, analysis)))
    for item in data.get("additives", []):
        claims.append(("Printed additive identifier", str(item.get("code", "")),
                       _observation_details(item, analysis)))
    if claims:
        sections.append(Section("Printed claims and additive identifiers", "table", claims,
                                columns=["Observation", "Value or printed statement", "Evidence and uncertainty"],
                                note="Printed claims are not independently certified. Symbols require visual verification. An additive identifier does not establish its ingredient identity or safety. OCR and extraction scores are not calibrated accuracy probabilities."))
    allergens = data.get("allergens", {})
    if allergens.get("concerns"):
        matches = [(m.get("concern", ""), m.get("matched_text", ""),
                    str(m.get("kind", "possible")).replace("_", " "), m.get("context", ""))
                   for m in allergens.get("matches", [])]
        note = "Concerns checked: " + ", ".join(allergens["concerns"]) + ". " + allergens.get("disclaimer", "")
        if allergens.get("unmatched"):
            note += " No text match for: " + ", ".join(allergens["unmatched"]) + "; this does not establish absence."
        sections.append(Section("Ingredient and allergen screening", "table", matches,
                                columns=["Concern", "Matched text", "Match type", "Label context"], note=note))
    return sections


def _observation_details(item: dict, analysis: Analysis) -> str:
    """Use recorded evidence rather than asserting a text suggestion as fact."""
    from .evidence import observation_sources

    status = str(item.get("status", "not independently verified")).replace("_", " ")
    parts = ["Status: " + status]
    if item.get("method"):
        parts.append("Method: " + str(item["method"]).replace("_", " "))
    confidence = item.get("ocr_confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        parts.append(f"OCR model score: {confidence:.2f} (not calibrated)")
    score = item.get("extraction_confidence")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        parts.append(f"Extraction score: {score:.2f} (heuristic)")
    sources = observation_sources(item)
    for source in sources:
        frame = source.get("frame")
        index = analysis.scan.frames.index(frame) if frame in analysis.scan.frames else None
        if index is None and not frame:
            candidate = source.get("image_index")
            if isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate < len(analysis.scan.frames):
                index = candidate
        reference = f"Image {index + 1}" if index is not None else "Source image not resolved"
        reference += "; panel " + str(source.get("panel", "unknown"))
        box = source.get("bbox")
        if box:
            reference += "; pixels " + ", ".join(str(v) for v in box)
        source_confidence = source.get("ocr_confidence")
        if isinstance(source_confidence, (int, float)) and not isinstance(source_confidence, bool):
            reference += f"; source OCR score {source_confidence:.2f} (not calibrated)"
        parts.append(reference)
        if source.get("text"):
            parts.append("Source text: " + str(source["text"]))
    if not sources:
        parts.append("No source location recorded; manual verification required.")
    if item.get("warning"):
        parts.append(str(item["warning"]))
    for alternative in item.get("ocr_alternatives", []):
        if alternative.get("text"):
            parts.append("Other OCR reading: " + str(alternative["text"]))
    return "\n".join(parts)
