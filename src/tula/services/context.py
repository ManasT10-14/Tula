"""Validate the officer's contextual facts independently from compliance verdicts."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

CATEGORIES = {"general", "food", "alcohol", "tobacco", "pan_masala", "medical_device", "cosmetic", "seed", "unknown"}
BUNDLES = {"single", "combination", "group", "multipiece", "unknown"}
SHAPES = {"rectangular", "cylindrical", "other", "unknown"}


def validate_context(raw, *, actor_id, assessed_on=None):
    if not isinstance(raw, dict):
        raise TypeError("Package context must be an object.")
    allowed = {"category", "category_confirmed", "bundle_type", "bundle_confirmed", "is_imported",
               "imported_confirmed", "exemption_confirmed", "shape", "shape_confirmed"}
    if set(raw) - allowed:
        raise ValueError("Unsupported package context fields.")
    for name, choices in (("category", CATEGORIES), ("bundle_type", BUNDLES), ("shape", SHAPES)):
        if not isinstance(raw.get(name, "unknown"), str) or raw.get(name, "unknown") not in choices:
            raise ValueError(f"Choose a valid {name.replace('_', ' ')}.")
    for name in allowed - {"category", "bundle_type", "shape"}:
        if name in raw and not isinstance(raw[name], bool):
            raise ValueError("Context confirmation values must be true or false.")
    for name, flag in (("category", "category_confirmed"), ("bundle_type", "bundle_confirmed"), ("shape", "shape_confirmed")):
        if raw.get(flag) and raw.get(name, "unknown") == "unknown":
            raise ValueError(f"Choose the {name.replace('_', ' ')} before confirming it.")
    if raw.get("imported_confirmed") and "is_imported" not in raw:
        raise ValueError("Record imported or domestic before confirming origin context.")
    return {**raw, "attested_by": actor_id,
            "assessment_date": (assessed_on or datetime.now(ZoneInfo("Asia/Kolkata")).date()).isoformat(),
            "assessment_date_confirmed": True}


def assessment_date(context, fallback=None):
    if context.get("assessment_date_confirmed") is True:
        try:
            return date.fromisoformat(context["assessment_date"])
        except (KeyError, TypeError, ValueError):
            pass
    return fallback


def form_context(category, bundle_type, origin, shape, confirmed):
    if origin not in ("unknown", "imported", "domestic"):
        raise ValueError("Choose imported, domestic or unconfirmed origin context.")
    result = {"category": category, "bundle_type": bundle_type, "shape": shape,
              "category_confirmed": confirmed and category != "unknown",
              "bundle_confirmed": confirmed and bundle_type != "unknown",
              "shape_confirmed": confirmed and shape != "unknown",
              "imported_confirmed": confirmed and origin != "unknown"}
    if origin != "unknown":
        result["is_imported"] = origin == "imported"
    return result
