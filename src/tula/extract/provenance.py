"""Primary declaration provenance and a read-only adapter for historical records."""
from __future__ import annotations

import math

from pydantic import ValidationError

from ..domain.enums import ExtractionPath
from ..domain.models import DeclarationProvenance, DeclarationSource


def _score(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 else None


def sources(values):
    """Retain source text even when legacy location metadata is incomplete."""
    output = []
    for value in values:
        if isinstance(value, DeclarationSource):
            source = value.model_copy(deep=True)
        elif isinstance(value, dict):
            try:
                source = DeclarationSource.model_validate({**value, "ocr_confidence": _score(value.get("ocr_confidence"))})
            except ValidationError:
                source = DeclarationSource(frame=value.get("frame") if isinstance(value.get("frame"), str) else None,
                                           text=str(value.get("text", "")), ocr_confidence=_score(value.get("ocr_confidence")))
        else:
            continue
        if source not in output:
            output.append(source)
    return output


def get_provenance(declaration):
    """Read legacy metadata without mutating declarations or historical JSON."""
    if declaration.provenance is not None:
        return declaration.provenance
    normalized = declaration.norm
    manual = declaration.path is ExtractionPath.MANUAL or normalized.get("verified_transcription") or normalized.get("extraction_method") == "inspector_correction"
    regions = sources(normalized.get("source_spans", []))
    if not regions and (declaration.frame or declaration.bbox):
        regions = sources([{"frame": declaration.frame, "bbox": declaration.bbox,
                            "panel": declaration.panel, "text": declaration.raw,
                            "ocr_confidence": None if manual else declaration.confidence}])
    if manual:
        return DeclarationProvenance(method="inspector_correction", status="officer_corrected",
            sources=[region.model_copy(update={"ocr_confidence": None, "method": "inspector_correction"}) for region in regions],
            score_basis="human_transcription_no_machine_score",
            original_transcription=normalized.get("original_ocr"),
            original_ocr_confidence=_score(declaration.confidence) if normalized.get("original_ocr") is not None else None)
    return DeclarationProvenance(method=normalized.get("extraction_method", "not_recorded"),
        status="cue_only" if not normalized else "needs_review" if normalized.get("requires_review") else "legacy_record",
        sources=regions, ocr_confidence=_score(declaration.confidence) if regions else None,
        extraction_confidence=_score(normalized.get("extraction_confidence")), score_basis="legacy_recorded_metadata")


def attach(declaration, source_values, *, method):
    """Build scores from every contributing line while preserving cue-only norm={}."""
    regions = sources(source_values)
    scores = [region.ocr_confidence for region in regions]
    confidence = min(scores) if scores and all(value is not None for value in scores) else None
    normalized = declaration.norm
    status = "cue_only" if not normalized else "needs_review" if normalized.get("requires_review") or (normalized.get("count") or 0) > 1 else "detected"
    if not regions or any(not region.frame or not region.bbox for region in regions):
        status = "unlocated"
    factor = .88 if method == "spatial_keyword_value" else .9 if method in {"contact_block", "brand_layout_heuristic"} else .97
    score = round(confidence * factor, 4) if confidence is not None and normalized else None
    declaration.provenance = DeclarationProvenance(method=method, status=status, sources=regions,
        ocr_confidence=confidence, extraction_confidence=score,
        score_basis=f"minimum contributing OCR score × {factor:g}; uncalibrated heuristic" if score is not None else "no_resolved_extraction_score")
    # Existing machine consumers keep the field confidence, now conservative for blocks.
    declaration.confidence = confidence if confidence is not None else 0.0
    if normalized:
        normalized.update(extraction_method=method, extraction_confidence=score,
                          source_spans=[region.model_dump(mode="json") for region in regions])
    return declaration


def intelligence_record(declaration):
    provenance = get_provenance(declaration)
    return {"value": declaration.norm, "raw": declaration.raw,
            "sources": [source.model_dump(mode="json") for source in provenance.sources],
            "ocr_confidence": provenance.ocr_confidence,
            "extraction_confidence": provenance.extraction_confidence,
            "method": provenance.method, "status": provenance.status}


def corrected_provenance(before, source, *, original=None):
    """Keep the first machine observation across repeated officer corrections."""
    current = get_provenance(before) if before is not None else None
    if current and current.method == "inspector_correction":
        raw, regions, confidence = current.original_transcription, current.original_sources, current.original_ocr_confidence
        # Old corrections may have retained only the immediately preceding text.
        # An actual revision-zero declaration, when available, is authoritative.
        if before.provenance is None and original is not None:
            first = get_provenance(original)
            if first.method != "inspector_correction":
                raw, regions, confidence = original.raw, first.sources, first.ocr_confidence
    elif current:
        raw, regions, confidence = before.raw, current.sources, current.ocr_confidence
    else:
        raw, regions, confidence = None, [], None
    return DeclarationProvenance(method="inspector_correction", status="officer_corrected",
        sources=sources([{**source, "ocr_confidence": None, "method": "inspector_correction"}]),
        score_basis="human_transcription_no_machine_score", original_transcription=raw,
        original_sources=regions, original_ocr_confidence=confidence)
