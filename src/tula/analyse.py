"""The orchestrator: captures in, adjudicated Analysis out.

This is the "one command turns a photo into a report" path. It deliberately
reads top-to-bottom in the order the PRD's pipeline table lists, so the code
and the architecture diagram stay the same shape:

    capture -> OCR -> extraction -> applicability -> scale -> measurement
            -> rules -> forensics

Every stage degrades rather than raises. A missing scale reference costs you
Tier B, not the run; a missing barcode costs you the GTIN checks, not the run.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from .demo import fixtures as demo_fixtures
from .domain.enums import (
    AssuranceTier,
    CaptureCompleteness,
    Lane,
    Panel,
    Severity,
    Verdict,
)
from .domain.enums import (
    DeclarationClass as DC,
)
from .domain.models import (
    Analysis,
    Citation,
    EvidenceCoverage,
    Finding,
    Measured,
    PackageFacts,
    ScaleEstimate,
    Scan,
    sha256_file,
    short_hash,
)
from .extract import normalizers
from .extract import pipeline as extract_pipeline
from .extract.pipeline import CUES
from .forensics import gtin as gtin_forensics
from .forensics.barcodes import decode as decode_barcodes
from .forensics.barcodes import locate as locate_barcodes
from .imaging import metrology
from .observability import log_event
from .ocr.engines import get_engine
from .rules import exemptions, scale_free
from .rules.engine import RulesEngine

# Above this share of a frame's regions carrying competing OCR candidates, the
# transcript as a whole is untrustworthy and nothing may be concluded absent
# from it. Below it, the contested regions are the frame's difficult corners --
# a dense nutrition table, an inkjet code -- and each is already barred from
# founding a finding on its own. A real capture of an Indian back panel runs at
# roughly a fifth to a quarter contested, so a frame past two-fifths is one
# where the recogniser is guessing more often than reading.
CONTESTED_FRAME_SHARE = 0.4


@dataclass
class Capture:
    """One image and which face of the package it shows."""

    path: str
    panel: Panel = Panel.PDP


@dataclass
class CaptureMeta:
    """Optional per-image sidecar (`<image>.meta.json`).

    Carries what the camera knew and the pipeline cannot recover from pixels:
    a depth-derived scale, the physical size of the panel, the packing date.
    On a real device the Android app fills this in; in the repo it is how a
    fixture exercises the Tier B path without a camera.
    """

    panel: Panel | None = None
    mm_per_px: float | None = None
    mm_per_px_source: str = "device_depth"
    pdp_width_mm: float | None = None
    pdp_height_mm: float | None = None
    package_span_px: float | None = None
    is_blown_formed: bool = False
    packing_date: date | None = None
    tilt_deg: float = 0.0
    marker_mm: float = 25.0
    # Set when this frame is known to contain every printed face -- flat
    # artwork, a rendered label, a dieline proof. Lets a single image prove a
    # declaration absent, which a photograph of one face of a box may not.
    covers_all_declarations: bool = False
    notes: str = ""

    @classmethod
    def load(cls, image_path: str) -> CaptureMeta:
        path = Path(image_path).with_suffix(".meta.json")
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("capture metadata must be a JSON object")
        if raw.get("mm_per_px_source") == "artwork":
            raise ValueError("Raster captures cannot claim an exact vector artwork scale")
        for key in ("mm_per_px", "pdp_width_mm", "pdp_height_mm", "package_span_px", "marker_mm"):
            value = raw.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be finite and positive")
        tilt = float(raw.get("tilt_deg", 0))
        if not math.isfinite(tilt) or abs(tilt) >= 75:
            raise ValueError("tilt_deg must be finite and within (-75, 75)")
        packing = raw.get("packing_date")
        return cls(
            panel=Panel(raw["panel"]) if raw.get("panel") else None,
            mm_per_px=raw.get("mm_per_px"),
            mm_per_px_source=raw.get("mm_per_px_source", "device_depth"),
            pdp_width_mm=raw.get("pdp_width_mm"),
            pdp_height_mm=raw.get("pdp_height_mm"),
            package_span_px=raw.get("package_span_px"),
            is_blown_formed=bool(raw.get("is_blown_formed", False)),
            packing_date=date.fromisoformat(packing) if packing else None,
            tilt_deg=float(raw.get("tilt_deg", 0.0)),
            marker_mm=float(raw.get("marker_mm", 25.0)),
            covers_all_declarations=bool(raw.get("covers_all_declarations", False)),
            notes=raw.get("notes", ""),
        )


@dataclass
class AnalyseOptions:
    lane: Lane = Lane.FIELD
    operator: str | None = None
    packing_date: date | None = None
    engine_name: str | None = None
    rules_dir: str | None = None
    geo: tuple[float, float] | None = None
    device: str | None = None
    extra_scales: list[ScaleEstimate] = field(default_factory=list)
    # The operator's attestation that every printed face of the package is in
    # the uploaded set. It is a statement of fact about the capture, made by the
    # person who made it, and it is what licenses a finding of absence.
    capture_is_complete: bool = False
    allergen_concerns: list[str] = field(default_factory=list)
    progress: Callable[[str, str], None] | None = None
    legal_context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------


def analyse(
    captures: list[Capture],
    options: AnalyseOptions | None = None,
    *,
    engine=None,
    rules: RulesEngine | None = None,
) -> Analysis:
    started = time.perf_counter()
    options = options or AnalyseOptions()
    engine = engine or get_engine(options.engine_name)
    rules = rules or RulesEngine.from_directory(options.rules_dir)

    warnings: list[str] = []
    def stage(name, detail):
        log_event(
            logging.getLogger("tula.pipeline"),
            logging.INFO,
            "analysis_stage",
            stage=name,
            frames=len(captures),
            total_elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        if options.progress:
            options.progress(name, detail)

    stage("received", "Images received; validating capture metadata")
    if not captures:
        raise ValueError("At least one capture is required")

    # A recorded result for this exact set of photographs, if one exists. Placed
    # here so every caller -- console, queue, CLI -- behaves identically, and
    # checked against the live pack version and engine so a stale recording is
    # never shown as though it were what this build decides. See demo/fixtures.
    replayed = demo_fixtures.lookup(
        [c.path for c in captures],
        rules_version=rules.pack.version,
        engine=getattr(engine, "name", "unknown"),
    )
    if replayed is not None:
        stage("complete", f"Stored demonstration result: {replayed.demo_fixture}")
        return replayed.model_copy(update={
            "elapsed_ms": int((time.perf_counter() - started) * 1000)})
    captures = [Capture(c.path, c.panel) for c in captures]
    metas = {}
    for c in captures:
        try:
            metas[c.path] = CaptureMeta.load(c.path)
        except (ValueError, TypeError, OSError) as exc:
            warnings.append(f"{Path(c.path).name}: invalid capture metadata ignored ({exc})")
            metas[c.path] = CaptureMeta()
    for capture in captures:
        meta = metas[capture.path]
        if meta.panel is not None:
            capture.panel = meta.panel

    # ---- 1. recognition -------------------------------------------------
    frame_hashes = {c.path: sha256_file(c.path) if Path(c.path).is_file() else "" for c in captures}
    results = []
    diagnostics = []
    unreadable_frames = []
    for capture_index, capture in enumerate(captures):
        stage("ocr", f"Reading image {capture_index + 1} of {len(captures)}; comparing OCR candidates")
        try:
            result = engine.read(capture.path)
        except Exception as exc:  # noqa: BLE001 - OCR adapter errors become recorded evidence gaps
            warnings.append(f"{Path(capture.path).name}: OCR failed ({exc})")
            unreadable_frames.append(capture.path)
            continue
        for line in result.lines:
            line.frame = capture.path
        diagnostics.append({"frame": capture.path, "panel": capture.panel.value,
                            "width": result.width, "height": result.height,
                            "quality": getattr(result, "quality", {}),
                            "candidates": getattr(result, "candidates", []),
                            "conflicts": getattr(result, "conflicts", []),
                            "passes": getattr(result, "passes", [])})
        # "Unreadable" governs one question only: may this capture establish
        # that a declaration is *absent*? A recogniser conflict in some region
        # is not that. This frame read 77 lines, 17 of them with competing
        # candidates -- overwhelmingly nutrition cells -- and condemning the
        # whole frame for it made every presence check on the pack inconclusive
        # with the message "only 76 lines were legible", which was both wrong
        # and unactionable. A conflicting *region* still cannot found a finding;
        # that is enforced per declaration, where it belongs. The frame fails
        # only when the transcript as a whole cannot be trusted.
        legible = sum(l.confidence >= EvidenceCoverage.LEGIBLE_CONFIDENCE and bool(l.text.strip())
                      for l in result.lines)
        conflicts = len(getattr(result, "conflicts", []))
        contested = conflicts / len(result.lines) if result.lines else 0.0
        if (contested > CONTESTED_FRAME_SHARE or
                getattr(result, "quality", {}).get("requires_rescan") or
                any(p.get("status") == "failed" for p in getattr(result, "passes", []))):
            unreadable_frames.append(capture.path)
        if legible < EvidenceCoverage.MIN_LEGIBLE_LINES:
            unreadable_frames.append(capture.path)
        warnings.extend(f"{Path(capture.path).name}: {w}" for w in result.warnings)
        results.append((capture.panel, result))

    # ---- 2. extraction --------------------------------------------------
    stage("extraction", "OCR complete; locating and normalizing declarations")
    extraction = extract_pipeline.extract(results, allergen_concerns=options.allergen_concerns)
    warnings.extend(extraction.warnings)
    declarations = extraction.declarations
    # A declaration held for review is a statement about that declaration, not
    # about the photograph it came from. Promoting it to "this frame is
    # unreadable" made one uncertain reading suppress every unrelated rule on
    # the scan: an allergen sentence misread as a country of origin took the
    # manufacturer, the net quantity and the MRP down with it. The hold already
    # does its work where it is meaningful -- `requires_review` stops that
    # declaration founding a finding, and the rule reports why.
    #
    # What does survive is disagreement *between frames*. Two photographs of the
    # same package that read its manufacture date as 03/2026 and 02/2026 have
    # not merely produced an uncertain declaration: at least one of them is not
    # showing what it appears to show, and neither may then be used to prove
    # that some other declaration is missing. That is a property of the pair,
    # which is why it is counted across frames rather than per declaration.
    for declaration in declarations.values():
        if not (declaration.norm.get("requires_review")
                or declaration.norm.get("date_evidence_uncertain")):
            continue
        frames = {source.get("frame")
                  for candidate in declaration.norm.get("candidates") or []
                  for source in candidate.get("sources") or []
                  if source.get("frame")}
        for event in (declaration.norm.get("date_events") or {}).values():
            if event.get("status") == "needs_review":
                frames |= {source.get("frame")
                           for observation in event.get("observations", [])
                           for source in observation.get("sources", []) if source.get("frame")}
        if len(frames) > 1:
            unreadable_frames.extend(frames)

    # ---- 3. applicability and exemptions -------------------------------
    # Runs before anything is evaluated. A 5 g sachet must come back "exempt",
    # not with four violations that do not exist in law.
    stage("applicability", "Classifying the package and selecting applicable rules")
    packing_date = options.packing_date or next(
        (m.packing_date for m in metas.values() if m.packing_date), None
    )
    dt = declarations.get(DC.DATE_OF_PACKING)
    if dt and dt.norm.get("date_evidence_uncertain"):
        packing_date = None
        warnings.append("The manufacture/packing event used for dated applicability has conflicting or uncertain evidence; verify the event before selecting a legal date.")
    elif packing_date is None and dt:
        event = dt.norm.get("date_events", {}).get(dt.norm.get("legal_date_role"), {})
        value = event.get("value") if event else dt.norm if not dt.norm.get("requires_review") else None
        if value and value.get("year"):
            packing_date = date(int(value["year"]), int(value.get("month") or 1), 1)
    if packing_date and packing_date > datetime.now(UTC).date():
        warnings.append("Packing date is in the future; rule applicability requires review.")
        packing_date = None
    from .services.context import assessment_date
    determination = exemptions.determine(
        declarations, raw_text=extraction.full_text, legal_context=options.legal_context,
        as_of=assessment_date(options.legal_context, packing_date),
        policy=getattr(rules.pack, "legal_policy", {}))

    decoded = []
    for capture in captures:
        try:
            decoded.extend(decode_barcodes(capture.path))
        except (ImportError, OSError, RuntimeError) as exc:
            warnings.append(f"{Path(capture.path).name}: barcode decoding unavailable ({exc})")
    gtin_check = gtin_forensics.best_candidate(decoded or extraction.gtin_candidates)
    if len(set(decoded)) > 1:
        warnings.append("Multiple distinct retail barcodes decoded. Product identity requires review.")
    package = PackageFacts(
        klass=determination.package_class,
        exemptions=determination.exemptions,
        is_imported=extraction.is_imported,
        gtin=gtin_check.gtin if gtin_check and gtin_check.valid and len(set(decoded)) <= 1 else None,
        is_blown_formed=any(m.is_blown_formed for m in metas.values()),
        panels_captured=[c.panel for c in captures],
        legal_context=dict(options.legal_context),
    )
    package = exemptions.apply(package, determination)
    if options.legal_context.get("imported_confirmed"):
        package.is_imported = bool(options.legal_context.get("is_imported"))
    nq = declarations.get(DC.NET_QUANTITY)
    if nq:
        package = package.model_copy(
            update={
                "capacity_value": nq.norm.get("value_base"),
                "capacity_unit": nq.norm.get("unit_base"),
            }
        )

    # ---- 4. scale estimation -------------------------------------------
    # The panel's pixel extent is knowable from the photograph alone. It is
    # measured before any scale is chosen, because it is what makes the
    # geometry prior reachable and what the scale-free screen runs on.
    extents = _panel_extents(captures, extraction.spans)
    scales = []
    frame_scales = {}
    for capture in captures:
        local_options = AnalyseOptions(extra_scales=[s for s in options.extra_scales if s.frame == capture.path or (s.frame is None and len(captures) == 1)])
        estimates = _estimate_scales([capture], {capture.path: metas[capture.path]}, package, extraction, local_options, extents)
        estimates = [s.model_copy(update={"frame": capture.path}) for s in estimates]
        scales.extend(estimates)
        frame_scales[capture.path] = metrology.fuse(estimates)
    if not any(frame_scales.values()):
        warnings.append(
            "No scale reference was available, so no physical measurement could be "
            "made. Rules requiring Tier B evidence will report as inconclusive. "
            "Include the printed LM Scale Card in frame to resolve this."
        )

    # ---- 5. measurement -------------------------------------------------
    measurements: dict[str, Measured] = {}
    stage("measurement", "Checking source scale and typography measurements")
    pdp_capture = next((c for c in captures if c.panel is Panel.PDP), None)
    measurements.update(_measure(declarations, extraction.spans, captures, metas, frame_scales, pdp_capture))
    pdp_scale = frame_scales.get(pdp_capture.path) if pdp_capture else None
    if pdp_scale:
        area = _pdp_area(metas, pdp_scale, pdp_capture, extents)
        if area is not None:
            package = package.model_copy(update={"pdp_area_cm2": area})

    # ---- 6. scan record --------------------------------------------------
    coverage = _coverage(captures, metas, extraction, options)
    coverage = coverage.model_copy(update={"unreadable_frames": list(dict.fromkeys(unreadable_frames))})
    if not coverage.is_legible:
        warnings.append(
            "The capture is too degraded to establish that any declaration is "
            "absent. Presence checks will report as inconclusive rather than as "
            "violations."
        )
    elif not coverage.is_complete:
        warnings.append(
            "Only part of the package was captured, so a declaration that was not "
            "found may simply be on a face that was not photographed. Capture the "
            "remaining faces, or tick “whole package captured”, to allow the "
            "presence checks to reach a determination."
        )

    scan = Scan(
        scan_id=uuid.uuid4().hex[:10].upper(),
        lane=options.lane,
        captured_at=datetime.now(UTC),
        packing_date=packing_date,
        frames=[c.path for c in captures],
        frame_hashes=frame_hashes,
        geo=options.geo,
        device=options.device,
        operator=options.operator,
        tier=min((s.tier for s in frame_scales.values() if s), key=lambda t: t.rank, default=AssuranceTier.C),
        scales=scales,
        coverage=coverage,
        image_diagnostics=diagnostics,
    )

    # ---- 7. adjudication -------------------------------------------------
    stage("rules", "Evaluating deterministic rules and evidence requirements")
    findings = rules.evaluate_all(scan, package, declarations, measurements)
    findings.extend(_forensic_findings(scan, rules.pack.version, gtin_check, declarations))
    findings.extend(
        _scale_free_findings(rules, scan, package, declarations, findings, extraction.spans, captures, extents)
    )

    analysis = Analysis(
        scan=scan,
        package=package,
        declarations=declarations,
        spans=extraction.spans,
        measurements=measurements,
        findings=findings,
        rules_version=rules.pack.version,
        engine=getattr(engine, "name", "unknown"),
        warnings=warnings,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        intelligence=getattr(extraction, "intelligence", {}),
    )
    analysis.warnings.insert(0, determination.summary)
    if rules.pack.notes:
        analysis.warnings.insert(0, rules.pack.notes)
    stage("complete", "Analysis complete; saving the inspection for officer review")
    return analysis


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _coverage(
    captures: list[Capture],
    metas: dict[str, CaptureMeta],
    extraction,
    options: AnalyseOptions,
) -> EvidenceCoverage:
    """Decide what this capture is entitled to prove a negative about.

    Two independent judgements. *Coverage* is geometric: which faces of the
    package are in evidence. Attestation counts here -- an inspector who
    photographs a sachet front and back and says so is telling the truth about
    the object in their hand, and no amount of pixel analysis can establish that
    fact from the images alone. *Legibility* is measured, not attested: it comes
    from what the recogniser actually returned, so a blurred frame cannot be
    talked up by ticking a box.
    """

    panels = list(dict.fromkeys(c.panel for c in captures))
    known = [p for p in panels if p is not Panel.UNKNOWN]

    attested = options.capture_is_complete or any(
        m.covers_all_declarations for m in metas.values()
    )

    if not captures:
        completeness = CaptureCompleteness.NONE
    elif attested or {Panel.PDP, Panel.BACK, Panel.LEFT, Panel.RIGHT, Panel.TOP, Panel.BOTTOM}.issubset(set(known)):
        completeness = CaptureCompleteness.COMPLETE
    elif len(known) >= 2:
        completeness = CaptureCompleteness.PARTIAL
    elif len(known) == 1:
        completeness = CaptureCompleteness.SINGLE
    else:
        completeness = CaptureCompleteness.NONE

    return EvidenceCoverage(
        panels_captured=known,
        completeness=completeness,
        attested_complete=attested,
        frames=len(captures),
        lines_read=extraction.lines_read,
        legible_lines=extraction.legible_lines,
        mean_confidence=round(extraction.mean_confidence, 4),
    )


def _panel_extents(captures: list[Capture], spans) -> dict[str, metrology.PanelExtent]:
    """Bracket every captured panel's pixel extent from its own photograph.

    Nobody is asked to hold a ruler against the package for this. The floor is
    the hull of the text the recogniser actually read, the ceiling is the
    package boundary where it can be separated from its background and the
    frame otherwise, and the width of that bracket is carried forward as
    uncertainty rather than being averaged away.
    """
    extents: dict[str, metrology.PanelExtent] = {}
    for capture in captures:
        boxes = [
            tuple(span.bbox) for span in spans
            if span.frame == capture.path and span.bbox is not None
        ]
        extent = metrology.panel_extent_px(capture.path, boxes)
        if extent is not None:
            extents[capture.path] = extent
    return extents


def _estimate_scales(
    captures: list[Capture],
    metas: dict[str, CaptureMeta],
    package: PackageFacts,
    extraction,
    options: AnalyseOptions,
    extents: dict[str, metrology.PanelExtent] | None = None,
) -> list[ScaleEstimate]:
    """Collect every independent opinion about mm-per-pixel."""
    scales: list[ScaleEstimate] = list(options.extra_scales)
    extents = extents or {}

    for capture in captures:
        meta = metas[capture.path]

        # (a) whatever the device measured -- depth sensor, or exact artwork
        if meta.mm_per_px:
            source = meta.mm_per_px_source
            scales.append(
                ScaleEstimate(
                    source=source,
                    mm_per_px=meta.mm_per_px,
                    sigma=meta.mm_per_px * metrology.SOURCE_SIGMA_REL.get(source, 0.05),
                    tier=metrology.SOURCE_TIER.get(source, AssuranceTier.C),
                    detail=meta.notes or f"supplied by capture metadata ({source})",
                )
            )

        # (b) the printed LM Scale Card, if it is in frame
        aruco = metrology.from_aruco(capture.path, marker_mm=meta.marker_mm)
        if aruco is not None:
            scales.append(aruco)

        # (c) the package's own barcode, which is printed to a regulated width.
        # This is the only automatic scale that needs nothing of the officer:
        # no card, no depth sensor, no tape. It brackets rather than measures,
        # so it stays Tier C -- but it is independent of the density prior
        # below, so where both are available they fuse, and where the quantity
        # could not be read it is the only scale left.
        for symbol in locate_barcodes(capture.path):
            estimate = metrology.from_barcode(symbol.pixel_width, symbol.symbology)
            if estimate is not None:
                scales.append(estimate)
                break

        # (d) a known physical panel size, when the officer measured it. The
        # pixel span must be supplied with it: a panel extent recovered from
        # the image knows the package boundary to about ten percent, and
        # dividing a tape measurement by that would dress a Tier C estimate up
        # as the Tier B reference this source claims to be.
        if meta.pdp_width_mm and meta.package_span_px:
            ref = metrology.from_reference(
                meta.package_span_px, meta.pdp_width_mm, "aruco_card",
                detail=f"panel width {meta.pdp_width_mm:g} mm measured on site",
            )
            if ref is not None:
                scales.append(ref)

    # (e) last-resort prior from the declared quantity; cross-check only
    span = next((m.package_span_px for m in metas.values() if m.package_span_px), None)
    if span is None:
        span = next(
            (extents[c.path].span_px for c in captures if c.path in extents), None
        )
    if span:
        prior = metrology.from_geometry_prior(
            package.capacity_value, package.capacity_unit, span
        )
        if prior is not None:
            scales.append(prior)

    return scales


def _measure(
    declarations,
    spans,
    captures: list[Capture],
    metas: dict[str, CaptureMeta],
    frame_scales: dict[str, ScaleEstimate | None],
    pdp_capture: Capture | None,
) -> dict[str, Measured]:
    out: dict[str, Measured] = {}

    # Rule 8 requires the numerals of the net quantity declaration to meet the
    # minimum height -- not "one of them somewhere on the pack". A label that
    # prints the English declaration large and the Devanagari one at half the
    # size is non-compliant in Devanagari, so the smallest rendering governs.
    nq_candidates: list[Measured] = []
    for span in spans:
        if span.bbox is None or span.confidence < EvidenceCoverage.LEGIBLE_CONFIDENCE:
            continue
        if not CUES[DC.NET_QUANTITY].search(span.text):
            continue
        if normalizers.parse_net_quantity(span.text) is None:
            continue
        source = next((c for c in captures if c.path == span.frame), None)
        scale = frame_scales.get(source.path) if source else None
        if scale is None:
            continue
        metrics = metrology.measure_cap_height_px(
            source.path if source else None, span.bbox, fallback_px=span.height_px
        )
        if metrics is not None:
            nq_candidates.append(
                metrology.to_millimetres(
                    metrics, scale, quantity="net_quantity.numeral_cap_height",
                    tilt_deg=metas[source.path].tilt_deg if source else 0.0,
                )
            )

    nq = declarations.get(DC.NET_QUANTITY)
    if not nq_candidates and nq is not None:
        source = next((c for c in captures if c.path == nq.frame), None)
        scale = frame_scales.get(source.path) if source else None
        metrics = metrology.measure_cap_height_px(
            source.path if source else None, nq.bbox, fallback_px=_fallback_height(nq)
        )
        if metrics is not None and scale is not None:
            nq_candidates.append(
                metrology.to_millimetres(
                    metrics, scale, quantity="net_quantity.numeral_cap_height",
                    tilt_deg=metas[source.path].tilt_deg if source else 0.0,
                )
            )

    if nq_candidates:
        smallest = min(nq_candidates, key=lambda m: m.value)
        out["net_quantity_cap_height"] = (
            smallest
            if len(nq_candidates) == 1
            else smallest.model_copy(
                update={
                    "method": smallest.method
                    + f"; smallest of {len(nq_candidates)} renderings of the declaration"
                }
            )
        )

    # The one-millimetre floor of Rule 8(1) applies to every declaration, so
    # the governing number is the smallest of them.
    heights: list[Measured] = []
    for klass, decl in declarations.items():
        if klass is DC.BRAND or decl.bbox is None:
            continue
        source = next((c for c in captures if c.path == decl.frame), None)
        scale = frame_scales.get(source.path) if source else None
        if scale is None:
            continue
        metrics = metrology.measure_cap_height_px(
            source.path if source else None, decl.bbox, fallback_px=_fallback_height(decl)
        )
        if metrics is None:
            continue
        heights.append(
            metrology.to_millimetres(
                metrics, scale, quantity=f"{klass.value}.cap_height",
                tilt_deg=metas[source.path].tilt_deg if source else 0.0,
            )
        )
    if heights:
        smallest = min(heights, key=lambda m: m.value)
        out["min_declaration_height"] = smallest.model_copy(
            update={"quantity": "smallest_mandatory_declaration.cap_height"}
        )

    return out


def _fallback_height(declaration) -> float | None:
    """Pixel height carried over from OCR, used when no image is readable."""
    if declaration.bbox is None:
        return None
    _, y0, _, y1 = declaration.bbox
    return (y1 - y0) * 0.62 if y1 > y0 else None


def _capture_for_panel(captures: list[Capture], panel: Panel) -> Capture | None:
    return next((c for c in captures if c.panel is panel), None)


def _pdp_area(
    metas: dict[str, CaptureMeta],
    scale: ScaleEstimate,
    pdp: Capture | None,
    extents: dict[str, metrology.PanelExtent] | None = None,
) -> Measured | None:
    """Area of the principal display panel.

    Sides an officer measured on site win, because a tape against the package
    beats anything inferred from pixels. Where nobody measured, the panel we
    found in the image and the millimetre scale together give the area without
    a second visit -- at a wider uncertainty, which is exactly what decides
    whether the Table I band can be selected at all.
    """
    meta = metas.get(pdp.path) if pdp else None
    if meta and meta.pdp_width_mm and meta.pdp_height_mm:
        area = (meta.pdp_width_mm * meta.pdp_height_mm) / 100.0
        # sides measured physically on site: small, honest uncertainty
        rel = 0.02
        return Measured(
            quantity="pdp_area", value=area, uncertainty=2 * area * rel, unit="cm2",
            tier=scale.tier, sources=["on-site measurement"],
            method=f"{meta.pdp_width_mm:g} x {meta.pdp_height_mm:g} mm panel",
        )
    extent = (extents or {}).get(pdp.path) if pdp else None
    if extent is not None:
        return metrology.pdp_area_from_extent(extent, scale)
    return None


def _height_ratios(
    declarations, spans, captures: list[Capture], extent: metrology.PanelExtent
) -> list[scale_free.HeightRatio]:
    """Glyph height as a fraction of the panel, in pixels and nothing else.

    Both ends of the panel bracket are carried through, because a shortfall and
    a clearance are each safest when measured against the opposite bound.
    """
    floor_px = math.sqrt(extent.min_width_px * extent.min_height_px)
    ceiling_px = math.sqrt(extent.max_width_px * extent.max_height_px)
    if floor_px <= 0 or ceiling_px <= 0:
        return []
    ratios: list[scale_free.HeightRatio] = []
    for quantity, klass in (
        ("net_quantity_cap_height", DC.NET_QUANTITY),
        ("min_declaration_height", None),
    ):
        candidates: list[tuple[float, float]] = []
        for decl_class, decl in declarations.items():
            if klass is not None and decl_class is not klass:
                continue
            if klass is None and decl_class is DC.BRAND:
                continue
            if decl.bbox is None:
                continue
            source = next((c for c in captures if c.path == decl.frame), None)
            metrics = metrology.measure_cap_height_px(
                source.path if source else None, decl.bbox,
                fallback_px=_fallback_height(decl),
            )
            if metrics is not None and metrics.cap_height_px > 0:
                candidates.append((metrics.cap_height_px, metrics.spread_px))
        if not candidates:
            continue
        cap_px, spread_px = min(candidates, key=lambda pair: pair[0])
        ratios.append(
            scale_free.HeightRatio(
                quantity=quantity, cap_height_px=cap_px,
                panel_min_px=floor_px, panel_max_px=ceiling_px,
                sigma_px=math.hypot(spread_px, metrology.EDGE_SIGMA_PX),
            )
        )
    return ratios


def _barcode_span_bounds(
    scan: Scan, extent: metrology.PanelExtent
) -> tuple[float, float] | None:
    """Bracket the panel's physical size from the barcode-derived scale.

    The barcode brackets millimetres per pixel; the panel's pixel extent is
    measured from the same photograph. Their product brackets the panel in
    millimetres, and unlike the density prior it needs no assumption about what
    is inside the package. Widened by the estimate's own k=2 interval, so the
    bracket states the range the standard permits rather than a point.
    """
    estimate = next((s for s in scan.scales if s.source == "barcode_symbol"), None)
    if estimate is None:
        return None
    low_mm_per_px = max(estimate.mm_per_px - 2 * estimate.sigma, 1e-6)
    high_mm_per_px = estimate.mm_per_px + 2 * estimate.sigma
    floor_px = math.sqrt(extent.min_width_px * extent.min_height_px)
    ceiling_px = math.sqrt(extent.max_width_px * extent.max_height_px)
    if floor_px <= 0 or ceiling_px <= 0:
        return None
    return (floor_px * low_mm_per_px, ceiling_px * high_mm_per_px)


def _scale_free_findings(
    rules, scan: Scan, package: PackageFacts, declarations, findings,
    spans, captures: list[Capture], extents: dict[str, metrology.PanelExtent],
) -> list[Finding]:
    """Screen the height rules the ordinary evaluation could not decide.

    Only where a scale was genuinely unavailable. A rule that already reached
    a verdict on a real millimetre scale does not want a weaker second opinion
    printed beside it.
    """
    undecided = {
        f.rule_id for f in findings
        if f.verdict in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)
    }
    candidates = [r for r in scale_free.scale_dependent_rules(rules.pack) if r.id in undecided]
    if not candidates:
        return []
    pdp = _capture_for_panel(captures, Panel.PDP) or (captures[0] if captures else None)
    extent = extents.get(pdp.path) if pdp else None
    if extent is None:
        return []
    ratios = _height_ratios(declarations, spans, captures, extent)
    return scale_free.screen(
        rules.pack, scan, package, declarations, ratios,
        RulesEngine.build_facts, rules_version=rules.pack.version,
        measured_bounds=_barcode_span_bounds(scan, extent),
    )


def _forensic_findings(
    scan: Scan, rules_version: str, gtin_check, declarations
) -> list[Finding]:
    """Corroborative checks that sit alongside the statutory rules.

    These never carry a penalty reference. They tell an officer where to look;
    they do not assert an offence. They are still gated by lane, because a
    citizen photograph of a barcode is no more admissible than a citizen
    photograph of a price.
    """
    findings: list[Finding] = []
    scan_id = scan.scan_id
    if gtin_check is None:
        return findings

    citation = Citation(
        clause="Corroborative evidence (GS1 General Specifications)",
        text=(
            "A GTIN encodes a check digit and a company prefix allocated by a GS1 "
            "member organisation. Neither is a declaration required by the Rules; "
            "both are useful corroboration of the declarations that are."
        ),
    )

    if not gtin_check.valid:
        # A number one clipped digit away from validating is a framing problem,
        # not a fabricated barcode, and must never be reported as the latter.
        if gtin_check.looks_truncated or not gtin_check.length_valid:
            verdict = Verdict.UNVERIFIED
            message = "Barcode could not be read in full"
        elif not scan.coverage.is_legible:
            verdict = Verdict.UNVERIFIED
            message = "Barcode could not be read reliably"
        else:
            verdict = Verdict.UNVERIFIED
            message = "Barcode number does not validate"
        if verdict is Verdict.VIOLATION and scan.lane.advisory_only:
            verdict = Verdict.ADVISORY
        findings.append(
            Finding(
                finding_id=f"F-{scan_id}-{short_hash('gtin.checkdigit', 6)}",
                rule_id="FORENSIC.GTIN.CHECK_DIGIT",
                rules_version=rules_version,
                citation=citation,
                verdict=verdict,
                severity=Severity.MINOR,
                message=message,
                detail=gtin_check.summary,
                extracted=gtin_check.gtin,
                tier=scan.tier,
            )
        )
        return findings

    origin = declarations.get(DC.COUNTRY_OF_ORIGIN)
    conflict = gtin_forensics.origin_conflict(
        gtin_check, origin.norm.get("country") if origin else None
    )
    findings.append(
        Finding(
            finding_id=f"F-{scan_id}-{short_hash('gtin.prefix', 6)}",
            rule_id="FORENSIC.GTIN.PREFIX",
            rules_version=rules_version,
            citation=citation,
            verdict=Verdict.INCONCLUSIVE if conflict else Verdict.PASS,
            severity=Severity.MINOR,
            message=(
                "Barcode allocation differs from the declared origin; review only"
                if conflict
                else "Barcode check digit validates; origin is not established by its prefix"
            ),
            detail=conflict or gtin_check.summary,
            extracted=gtin_check.gtin,
        )
    )
    return findings
