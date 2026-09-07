"""Scoped legal-context decisions; all dates and parameters come from the pack.

These screens cannot establish applicability from an OCR keyword alone. Missing
facts affect only their dependent rules; ordinary presence and measurement
operators remain available. Decision reasons are retained in every finding.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlsplit

from ..domain.enums import Verdict


def context(facts: dict) -> dict:
    return getattr(facts.get("pkg"), "legal_context", {}) or {}


def _number(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def scope(rule, facts: dict, when: date, policy: dict) -> tuple[Verdict, str] | None:
    """Return a scoped exemption/review result, or allow normal evaluation."""
    legal = context(facts)
    check = rule.legal_check
    category = legal.get("category", "unknown")
    medical = policy.get("medical_devices", {})
    if category == "medical_device" and medical.get("from") and when >= date.fromisoformat(medical["from"]):
        return Verdict.INCONCLUSIVE, medical["reason"]

    for reason in legal.get("legal_review_reasons", []):
        return Verdict.INCONCLUSIVE, str(reason)

    # The pipeline never treats a historic manufacture/packing date as proof
    # that stock is governed by later label amendments merely because it is
    # being inspected today.
    packing = facts.get("ctx", {}).get("packing_date")
    transition = check.get("current_from") or check.get("manufacture_from")
    if check.get("kind") in ("unit_price", "origin"):
        transition = rule.effective_from.isoformat()
    date_declaration = facts.get("decl", {}).get("date_of_packing")
    date_evidence = getattr(date_declaration, "norm", {})
    if transition and date_evidence.get("date_evidence_uncertain"):
        return Verdict.INCONCLUSIVE, "Conflicting or uncertain manufacture/packing event evidence prevents dated applicability from being established. Verify the printed event and date; the capture date does not resolve this conflict."
    if packing and transition and packing < date.fromisoformat(transition) <= when:
        return Verdict.INCONCLUSIVE, "The recorded packing date predates the relevant amendment while assessment is later. Verify old-stock and transitional applicability before applying the current requirement."

    kind = check.get("kind")
    if kind == "manufacturer" and category in check.get("review_categories", []):
        return Verdict.INCONCLUSIVE, "This category has a separate manufacturer/importer declaration regime. Verify the applicable source and required parties."

    if kind == "date" and (legal.get("category_confirmed") is not True or category in check.get("review_categories", [])):
        return Verdict.INCONCLUSIVE, "Confirm commodity category and the applicable manufacture/packing/expiry requirements; food and other special-category date exceptions are not decided by this generic screen."

    if kind == "height":
        if category in check.get("review_categories", []):
            return Verdict.INCONCLUSIVE, "Confirm the category-specific declaration and glyph-height regime before applying this general label-height screen."
        if legal.get("shape_confirmed") is not True:
            return Verdict.INCONCLUSIVE, "Confirm package shape and the statutory PDP before applying the area-based height threshold."
        if legal.get("shape") != "rectangular":
            return Verdict.INCONCLUSIVE, "This measurement pipeline does not establish statutory PDP geometry for the confirmed non-rectangular package. Review the measured area under Rule 7(4)."

    if kind == "origin" and (legal.get("imported_confirmed") is not True or not isinstance(legal.get("is_imported"), bool)):
        return Verdict.INCONCLUSIVE, "Confirm whether this commodity was imported; OCR text, brand and barcode prefix cannot establish legal origin or import status."
    if kind == "origin" and legal.get("is_imported") is False:
        return Verdict.NOT_APPLICABLE, "Import status was confirmed as domestic; this imported-commodity origin screen does not apply."

    if kind == "rounding_history":
        if when > date.fromisoformat(check["deterministic_to"]):
            return Verdict.INCONCLUSIVE, "MRP rounding had a historical Rule 6(1)(e) provision. Its amendment, deferred commencement and early-adoption transition require legal review; no rounding offence is inferred here."
        return None

    if kind != "unit_price":
        return None
    if category == "alcohol":
        return Verdict.INCONCLUSIVE, "Verify the applicable State excise law before deciding the unit-price requirement for alcoholic liquor."
    if legal.get("bundle_confirmed") is True and legal.get("bundle_type") in check.get("exempt_bundles", []):
        return Verdict.EXEMPT, "Rule 6(11) unit-price exception: confirmed " + legal["bundle_type"] + " package. Other label duties remain subject to their own checks."
    decls = facts.get("decl", {})
    quantity = getattr(decls.get("net_quantity"), "norm", {})
    value = _number(quantity.get("value_base"))
    unit = quantity.get("unit_base")
    spec = check.get("bases", {}).get(unit)
    if spec is None or value is None or value <= 0:
        return Verdict.INCONCLUSIVE, "A positive net quantity and a supported statutory unit-price dimension are required."
    boundary = _number(spec.get("boundary"))
    # At one gram/ml/cm or one count, the unambiguous prescribed denominator
    # equals the net quantity. The kilogram/litre/metre equality boundary is
    # omitted by the replacement's strict </> wording and needs legal review.
    if value == Decimal(1):
        return Verdict.EXEMPT, "Rule 6(11) equal-price proviso: at this declared quantity the statutory unit price equals the package MRP; a separate unit-price declaration is not required."
    if boundary is not None and value == boundary:
        return Verdict.INCONCLUSIVE, "The net quantity is exactly at the kilogram/litre/metre boundary omitted by the replacement's strict less-than/greater-than wording. Confirm the equal-MRP exception or obtain legal interpretation before deciding this unit-price duty."
    if "unit_sale_price" not in decls and not (legal.get("bundle_confirmed") is True and legal.get("bundle_type") == "single"):
        return Verdict.INCONCLUSIVE, "Confirm whether this is a single, combination, group or multipiece package before treating an absent unit price as noncompliance."
    if legal.get("category_confirmed") is not True and "unit_sale_price" not in decls:
        return Verdict.INCONCLUSIVE, "Confirm commodity category, including any State-excise exception, before deciding that a unit price is required."
    # Unknown bundle applicability prevents an adverse inference, but need not
    # hide a correctly printed and arithmetically verified voluntary unit price.
    return None


def assertion(ctx, check: dict):
    """Evaluate a versioned legal screen with the interpreter's trace contract."""
    kind = check.get("kind")
    when = ctx.resolve("$ctx.assessment_date")
    if isinstance(when, str):
        when = date.fromisoformat(when)
    if kind == "consumer_care":
        decl = ctx.resolve("$decl.consumer_care")
        if decl is None:
            return False if ctx.absence_is_provable() else None
        fields = check["current_fields"] if when >= date.fromisoformat(check["current_from"]) else check["historical_fields"]
        missing = [name for name in fields if decl.norm.get("has_" + name) is not True]
        if not missing:
            ctx.trace["legal_detail"] = "The complaints contact block contains name/office, address and the required contact channels; postal completeness and reachability are not certified."
            return True
        ctx.trace["legal_detail"] = "Consumer-care components not established: " + ", ".join(missing) + ". Verify the complete linked complaints-contact block."
        # Automatic parsing is deliberately asymmetric. A verified transcript
        # can prove missing components; an OCR parse failure cannot do so.
        return False if decl.norm.get("verified_transcription") and ctx.absence_is_provable() else None
    if kind == "price_uniqueness":
        count = _number(ctx.resolve("$decl.retail_sale_price.norm.count"))
        if count is not None and count == 1:
            return True
        ctx.trace["legal_detail"] = "Retain and review each price and sticker: lower-price and date-limited tax revisions can lawfully coexist with the original MRP. Multiple OCR values alone do not prove an offence."
        return None
    if kind == "date":
        decl = ctx.resolve("$decl.date_of_packing")
        if decl is None:
            return False if ctx.absence_is_provable() else None
        if decl.norm.get("year") is None or decl.norm.get("month") is None:
            ctx.trace["parse_gap"] = "The date's month and year could not be read."
            return None
        if when >= date.fromisoformat(check["manufacture_from"]) and not re.search(check["manufacture_pattern"], decl.raw, re.IGNORECASE):
            ctx.trace["legal_detail"] = "A packing/import date was located, but the current generic rule requires a manufacture date. Confirm the date role and any applicable exception."
            return None
        return True
    if kind == "unit_price":
        declared = _number(ctx.resolve("$decl.unit_sale_price.norm.value"))
        price = _number(ctx.resolve("$decl.retail_sale_price.norm.value"))
        quantity = _number(ctx.resolve("$decl.net_quantity.norm.value_base"))
        basis = _number(ctx.resolve("$decl.unit_sale_price.norm.per_base"))
        unit = ctx.resolve("$decl.net_quantity.norm.unit_base")
        declared_unit = ctx.resolve("$decl.unit_sale_price.norm.unit_base")
        if unit != declared_unit:
            ctx.trace["arithmetic"] = "Unit-price dimensions differ or are unreadable; verify the declared units"
            return None
        spec = check.get("bases", {}).get(unit)
        if spec is None or any(n is None for n in (declared, price, quantity, basis)) or quantity <= 0 or price < 0 or declared < 0:
            return None
        boundary = _number(spec.get("boundary"))
        if boundary is not None and quantity == boundary:
            ctx.trace["legal_detail"] = "Exact statutory base-unit boundary requires the equal-price exception or legal interpretation."
            return None
        expected_basis = _number(spec["small"] if boundary is None or quantity < boundary else spec["large"])
        expected = (price / quantity * expected_basis).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ctx.trace["arithmetic"] = f"Declared {declared} per {basis} {unit}; prescribed basis {expected_basis} {unit}, computed price {expected} rounded to two decimals"
        return basis == expected_basis and declared == expected
    raise ValueError(f"Unknown legal screen {kind!r}")


def qualify_adverse(rule, facts: dict, verdict: Verdict, detail: str) -> tuple[Verdict, str]:
    """Missing contextual evidence cannot support an adverse legal conclusion."""
    if verdict is not Verdict.VIOLATION:
        return verdict, detail
    legal = context(facts)
    if rule.legal_check.get("kind") == "unit_price" and not (
        legal.get("bundle_confirmed") is True and legal.get("bundle_type") == "single" and legal.get("category_confirmed") is True
    ):
        return Verdict.INCONCLUSIVE, (detail + " Confirm category and single-package status before treating this unit-price mismatch as a required-declaration violation.").strip()
    if rule.legal_check.get("kind") == "script":
        return Verdict.INCONCLUSIVE, "The OCR script screen did not establish Hindi or English. Review the original with a language-capable reader before any adverse language finding."
    return verdict, detail


_CHECK_KEYS = {
    "consumer_care": {"kind", "current_from", "current_fields", "historical_fields"},
    "unit_price": {"kind", "exempt_bundles", "bases"},
    "date": {"kind", "manufacture_from", "manufacture_pattern", "review_categories"},
    "price_uniqueness": {"kind"},
    "manufacturer": {"kind", "review_categories"},
    "height": {"kind", "review_categories"},
    "origin": {"kind"},
    "rounding_history": {"kind", "deterministic_to"},
    "script": {"kind"},
}
_SCREEN_KINDS = {"consumer_care", "unit_price", "date", "price_uniqueness"}


def _object(value, keys, label, *, required=True):
    if not isinstance(value, dict) or set(value) - keys or (required and keys - set(value)):
        raise ValueError(f"{label} requires exactly these keys: {sorted(keys)}")


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")  # noqa: TRY004 - public validation error contract
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _strings(value, label, allowed=None):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in value):
        raise ValueError(f"{label} must contain bounded nonempty strings")
    if len(set(value)) != len(value) or (allowed is not None and set(value) - allowed):
        raise ValueError(f"{label} has duplicate or unsupported values")


def _validate_check(check):
    if not isinstance(check, dict) or check.get("kind") not in _CHECK_KEYS:
        raise ValueError("Unknown legal-check kind")
    kind = check["kind"]
    _object(check, _CHECK_KEYS[kind], "legal_check")
    for key in ("current_from", "manufacture_from", "deterministic_to"):
        if key in check:
            _date(check[key], key)
    if "review_categories" in check:
        _strings(check["review_categories"], "review_categories", {"general", "food", "alcohol", "tobacco", "pan_masala", "medical_device", "drug", "cosmetic", "seed", "garment", "unknown"})
    if kind == "consumer_care":
        for key in ("current_fields", "historical_fields"):
            _strings(check[key], key, {"name", "address", "phone", "email"})
            if not check[key]:
                raise ValueError("A contact screen must require at least one component")
    if kind == "date":
        pattern = check["manufacture_pattern"]
        # A list of ordinary date cue words avoids executing administrator-
        # supplied arbitrary regex syntax against long OCR transcripts.
        if pattern != r"\b(?:manufactur(?:e|ed|ing)|mfg|mfd)\b":
            raise ValueError("Unsupported manufacture date cue pattern")
    if kind == "unit_price":
        _strings(check["exempt_bundles"], "exempt_bundles", {"combination", "group", "multipiece"})
        _object(check["bases"], {"g", "ml", "cm", "N"}, "unit-price bases")
        for unit, spec in check["bases"].items():
            _object(spec, {"small"} if unit == "N" else {"small", "large", "boundary"}, "unit-price basis")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or _number(v) is None or not 0 < v <= 1000000 for v in spec.values()):
                raise ValueError("Unit-price basis values must be finite positive numbers")


def validate_legal_screen(arg: dict) -> None:
    """Strict validator used by the administrator's expression importer."""
    _validate_check(arg)
    if arg["kind"] not in _SCREEN_KINDS:
        raise ValueError("This legal-check kind is a context gate, not a legal_screen expression")


def validate_legal_metadata(pack) -> None:
    """Reject unknown or malformed nested legal metadata before activation."""
    for rule in pack.rules:
        if rule.legal_check:
            _validate_check(rule.legal_check)
        def check_expression(node, expected_check=rule.legal_check):
            if isinstance(node, dict):
                if "legal_screen" in node:
                    validate_legal_screen(node["legal_screen"])
                    if node["legal_screen"] != expected_check:
                        raise ValueError("legal_screen parameters must match their rule's legal_check context")
                for value in node.values():
                    check_expression(value)
            elif isinstance(node, list):
                for value in node:
                    check_expression(value)
        check_expression(rule.assertion)
        check_expression(rule.applies_when)
        for item in rule.sources:
            _object(item, {"url", "notification", "publication_date", "clause", "review_status"}, "rule source")
            if any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in item.values()):
                raise ValueError("Rule source values must be bounded nonempty strings")
            url = urlsplit(item["url"])
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                raise ValueError("Rule source must use an ordinary HTTP(S) URL")
            _date(item["publication_date"], "publication_date")
    if not pack.legal_policy:
        return  # preserved historical pack has no new policy metadata
    policy = pack.legal_policy
    _object(policy, {"medical_devices", "exemptions", "enforcement"}, "legal_policy")
    _object(policy["medical_devices"], {"from", "reason"}, "medical_devices policy")
    _object(policy["enforcement"], {"from", "reference", "reason"}, "enforcement policy")
    for section in ("medical_devices", "enforcement"):
        _date(policy[section]["from"], section + ".from")
        for key, value in policy[section].items():
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError(f"Invalid {section}.{key}")
    values = policy["exemptions"]
    _object(values, {"small_limit", "tobacco_from", "pan_masala_from", "ordinary_large_limit", "large_from", "institutional_terms", "tobacco_terms"}, "exemptions policy")
    for key in ("tobacco_from", "pan_masala_from", "large_from"):
        _date(values[key], key)
    for key in ("institutional_terms", "tobacco_terms"):
        _strings(values[key], key)
    for key in ("small_limit", "ordinary_large_limit"):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or _number(value) is None or not 0 < value <= 1000000000:
            raise ValueError(f"Invalid positive threshold {key}")
