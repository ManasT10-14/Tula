"""Evidence-linked officer corrections of supplementary label observations."""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime

from PIL import Image

from ..extract.intelligence import (
    BATCH_CUE,
    DATE_CUES,
    INGREDIENT_CUE,
    _date_value,
    _segments,
    analyze_allergens,
)
from ..report.evidence import verify
from .review import _editable, _invalidate, _reason

FIELD_LABELS = {
    "manufacturing_date": "Manufacturing date", "packing_date": "Packing date",
    "expiry_date": "Expiry date", "best_before": "Best before", "use_by": "Use by",
    "batch_number": "Batch / lot number", "ingredients": "Ingredients / allergen statement",
    "importer": "Importer details",
}


def _parsed(field, raw, interpretation):
    if field in DATE_CUES:
        cue = DATE_CUES[field]
        if not cue.search(raw) and any(pattern.search(raw) for key, pattern in DATE_CUES.items() if key != field):
            raise ValueError("The printed date cue does not match the selected observation. Choose the matching date field.")
        parsed = _date_value(_segments(raw, cue))
        if not parsed and not cue.search(raw):
            raise ValueError("Include the printed date cue and marking, or a recognizable date.")
        parsed = parsed or {"candidates": [], "status": "needs_review"}
        candidates = parsed["candidates"]
        value = candidates[0]["iso"] if len(candidates) == 1 and parsed["status"] == "detected" else None
        status = "officer_corrected" if parsed["status"] == "detected" else parsed["status"]
        warning = parsed.get("two_digit_year_assumption")
        if parsed.get("duration") is not None:
            if field not in {"best_before", "use_by", "expiry_date"}:
                raise ValueError("A manufacturing or packing date must not be replaced by a shelf-life duration.")
            value = {key: parsed[key] for key in ("duration", "unit", "reference")}
        elif not candidates:
            warning = "The corrected marking does not resolve to a valid calendar date; manual review remains required."
        if interpretation:
            if interpretation not in {candidate["iso"] for candidate in candidates}:
                raise ValueError("The selected interpretation must be one of the date candidates supported by the corrected text.")
            value, status = interpretation, "officer_interpreted"
            warning = "The officer selected a date interpretation; the original ambiguity and candidate dates are retained."
        return {"value": value, "status": status, "candidates": candidates,
                **({"warning": warning} if warning else {}),
                **({"date_interpretation": interpretation} if interpretation else {})}
    if interpretation:
        raise ValueError("Date interpretation applies only to a date observation.")
    if field == "batch_number":
        cue = BATCH_CUE.match(raw)
        labelled = cue and (cue.end() == len(raw) or raw[cue.end()].isspace() or raw[cue.end()] in ":#")
        value = (raw[cue.end():] if labelled else raw).strip(" :#-")
        if (not re.fullmatch(r"[A-Z0-9][A-Z0-9./_ -]{0,79}", value, re.IGNORECASE)
                or value.casefold() in {"see bottom", "see base", "printed below", "number", "code"}):
            raise ValueError("Enter the complete printed batch or lot code.")
    elif field == "ingredients":
        value = INGREDIENT_CUE.sub("", raw, count=1).strip(" :;-\n")
        if not value:
            raise ValueError("Enter the ingredient or allergen statement, not just its heading.")
    else:
        value = raw
    return {"value": value, "status": "officer_corrected", "candidates": []}


def correct_observation(repo, scan_id, revision, actor, field, raw, frame_index, bbox,
                        reason, *, observation_index=-1, date_interpretation=""):
    """Update one observation or add one, without mutating statutory declarations."""
    reason = _reason(reason)
    if field not in FIELD_LABELS:
        raise ValueError("Choose a supported supplementary observation. Use the main correction form for statutory declarations.")
    if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= 4000:
        raise ValueError("Enter the verified label text, up to 4,000 characters.")
    if type(observation_index) is not int or observation_index < -1:
        raise ValueError("Choose a current observation or add a new one.")
    if type(frame_index) is not int:
        raise ValueError("Choose a source image.")
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(type(value) is not int for value in bbox)):
        raise ValueError("Enter four whole-number pixel bounds for the evidence rectangle.")
    raw = raw.strip()
    parsed = _parsed(field, raw, date_interpretation.strip())

    def change(a):
        _editable(a, actor)
        if not 0 <= frame_index < len(a.scan.frames):
            raise ValueError("Choose a source image.")
        if any(record["status"] != "verified" for record in verify(a)):
            raise ValueError("Evidence is missing or changed; restore the originals and working images before correcting.")
        frame = a.scan.frames[frame_index]
        with Image.open(frame) as image:
            width, height = image.size
        if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
            raise ValueError("Select a valid evidence rectangle inside the source image.")
        observations = a.intelligence.setdefault("fields", {}).setdefault(field, [])
        if observation_index >= len(observations):
            raise ValueError("This observation is no longer current. Reload the inspection.")
        before = deepcopy(observations[observation_index]) if observation_index >= 0 else None
        prior_sources = before.get("sources", []) if before else []
        panel = next((record.get("panel", "unknown") for record in a.scan.capture_edits
                      if record.get("frame") == frame),
                     next((record.get("panel", "unknown") for record in a.scan.image_diagnostics
                           if record.get("frame") == frame),
                          next((source.get("panel", "unknown") for source in prior_sources
                                if source.get("frame") == frame), "unknown")))
        confidence = before.get("ocr_confidence") if before else None
        source = {"frame": frame, "image_index": frame_index, "image_width": width,
                  "image_height": height, "panel": panel, "bbox": list(bbox), "text": raw,
                  "ocr_confidence": confidence, "method": "inspector_correction"}
        original = (before.get("original_transcription", before.get("raw")) if before else None)
        original_sources = deepcopy(before.get("original_sources", prior_sources)) if before else []
        now = datetime.now(UTC).isoformat()
        after = {**parsed, "raw": raw, "sources": [source], "method": "inspector_correction",
                 "ocr_confidence": confidence, "extraction_confidence": None,
                 "original_transcription": original, "original_sources": original_sources,
                 "ocr_alternatives": deepcopy(before.get("ocr_alternatives", [])) if before else [],
                 "corrected_by": str(actor.id), "corrected_by_name": actor.display_name,
                 "corrected_at": now, "correction_reason": reason}
        if observation_index >= 0:
            observations[observation_index] = after
            index = observation_index
        else:
            index = len(observations)
            observations.append(after)
        if field == "ingredients":
            concerns = a.intelligence.get("allergens", {}).get("concerns", [])
            a.intelligence["allergens"] = analyze_allergens(observations, concerns)
            # Additive identifiers are derived from the ingredient statements too.
            a.intelligence["additives"] = [
                {"code": match[0], "sources": deepcopy(item.get("sources", [])),
                 "method": "printed_additive_identifier", "status": item.get("status", "needs_review"),
                 "ocr_confidence": item.get("ocr_confidence"),
                 "extraction_confidence": item.get("extraction_confidence")}
                for item in observations
                for match in re.finditer(r"\b(?:INS\s*[-:]?\s*\d{3,4}[a-z]?|E\s*-?\s*\d{3,4}[a-z]?)\b",
                                         str(item.get("value") or item.get("raw", "")), re.IGNORECASE)
            ]
        multiple_warning = f"Multiple {field.replace('_', ' ')} values were detected; verify the original markings."
        warnings = a.intelligence.setdefault("warnings", [])
        warnings[:] = [warning for warning in warnings if warning != multiple_warning]
        if field not in {"ingredients", "importer"} and len({str(item["value"]) for item in observations
                                                              if item.get("value") is not None}) > 1:
            warnings.append(multiple_warning)
        a.review.corrections.append({"kind": "intelligence." + field, "observation_index": index,
                                    "before": before, "after": deepcopy(after), "actor_id": str(actor.id),
                                    "actor": actor.display_name, "reason": reason, "created_at": now})
        a.review.comments.append({"actor_id": str(actor.id), "actor": actor.display_name,
                                  "action": "intelligence_corrected", "created_at": now,
                                  "text": f"{FIELD_LABELS[field]} observation corrected: {reason} "
                                          "Primary statutory declarations and rule findings were not changed; "
                                          "use the main declaration correction workflow if those also need correction."})
        _invalidate(a.review)

    return repo.revise(scan_id, revision, str(actor.id), "intelligence.corrected", reason, change)
