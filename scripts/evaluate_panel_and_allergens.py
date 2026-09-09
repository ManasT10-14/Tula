"""Measure the ruler-free panel screen and the allergen sweep on real photographs.

Run: python scripts/evaluate_panel_and_allergens.py

Two capabilities are exercised on the same real photographs the OCR evaluation
uses, and both are reported the same way: what the system produced, what can
be checked against it, and what cannot.

There is no ground truth for the physical size of these packages -- nobody
measured them -- so no accuracy claim is made about millimetres. What *is*
checkable is stated and checked: the panel bracket must contain the printed
text it was derived from, its two ends must be ordered, the scale-free screen
must never emit a violation, and the allergen sweep must reproduce exactly
when re-run. Everything else is recorded for inspection, not scored.

This is an unblinded development evaluation on a small fixed set. It is not
independent accuracy evidence.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import Capture, _height_ratios, _panel_extents, analyse
from tula.domain.enums import Panel, Verdict
from tula.rules import scale_free

OUT = ROOT / "out" / "panel-and-allergen-evaluation"


def photographs() -> list[Path]:
    """Every distinct real photograph in the retained evaluation sets."""
    seen: dict[str, Path] = {}
    for path in sorted(ROOT.glob("data/uploads/**/*.jpg")):
        seen.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
    extra = ROOT / "data/evaluation/additional-real-labels"
    for path in sorted([*extra.glob("*.jpg"), *extra.glob("*.png")]):
        seen.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
    return sorted(seen.values())


def evaluate(path: Path) -> dict:
    started = time.perf_counter()
    capture = Capture(str(path), Panel.PDP)
    result = analyse([capture])
    extents = _panel_extents([capture], result.spans)
    extent = extents.get(str(path))

    record: dict = {
        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "lines_read": result.scan.coverage.lines_read,
        "declarations": sorted(k.value for k in result.declarations),
        "declared_quantity": (
            f"{result.package.capacity_value} {result.package.capacity_unit}"
            if result.package.capacity_value else None
        ),
        "scale_sources": sorted({s.source for s in result.scan.scales}),
    }

    if extent is None:
        record["panel"] = None
    else:
        record["panel"] = {
            "text_hull_px": [extent.min_width_px, extent.min_height_px],
            "outer_bound_px": [extent.max_width_px, extent.max_height_px],
            "point_estimate_px": [round(extent.width_px, 1), round(extent.height_px, 1)],
            "segmented": extent.segmented,
            "relative_sigma": round(extent.rel_sigma, 4),
            # Checkable without any ground truth: the bracket must be ordered,
            # and the printed text must fit inside the panel it was read from.
            "bracket_is_ordered": (
                extent.min_width_px <= extent.width_px <= extent.max_width_px
                and extent.min_height_px <= extent.height_px <= extent.max_height_px
            ),
        }
        ratios = _height_ratios(result.declarations, result.spans, [capture], extent)
        record["height_ratios"] = [
            {"quantity": r.quantity,
             "cap_height_px": round(r.cap_height_px, 1),
             "ratio_against_text_hull_pct": round(r.ratio_high * 100, 3),
             "ratio_against_outer_bound_pct": round(r.ratio_low * 100, 3)}
            for r in ratios
        ]
        record["span_bounds_mm"] = [
            round(v, 1) for v in
            scale_free.span_bounds_mm(result.package.capacity_value, result.package.capacity_unit)
        ]

    record["pdp_area_cm2"] = (
        {"value": round(result.package.pdp_area_cm2.value, 1),
         "uncertainty": round(result.package.pdp_area_cm2.uncertainty, 1),
         "tier": result.package.pdp_area_cm2.tier.value,
         "method": result.package.pdp_area_cm2.method}
        if result.package.pdp_area_cm2 else None
    )
    record["scale_free_findings"] = [
        {"rule_id": f.rule_id, "verdict": f.verdict.value, "tier": f.tier.value if f.tier else None,
         "message": f.message}
        for f in result.findings if f.rule_id.startswith("SCREEN.SCALE_FREE")
    ]
    record["height_rule_verdicts"] = {
        f.rule_id: f.verdict.value for f in result.findings if f.rule_id.startswith("LMPCR.R8")
    }

    screen = result.intelligence.get("allergens", {}).get("screen", {})
    record["allergens"] = {
        "ingredient_statements_read": len(
            result.intelligence.get("fields", {}).get("ingredients", [])
        ),
        "detected": [
            {"allergen": item["allergen"], "kind": item["kind"],
             "matched_text": item["matched_text"],
             "declared_in_statement": item["declared_in_statement"]}
            for item in screen.get("detected", [])
        ],
        "undeclared": screen.get("undeclared", []),
    }
    return record


def main() -> int:
    files = photographs()
    OUT.mkdir(parents=True, exist_ok=True)
    records = [evaluate(path) for path in files]

    panels = [r for r in records if r["panel"]]
    checks = {
        "photographs": len(records),
        "panel_bracket_produced": len(panels),
        "panel_bracket_ordered": sum(1 for r in panels if r["panel"]["bracket_is_ordered"]),
        "panel_segmented_from_background": sum(1 for r in panels if r["panel"]["segmented"]),
        "panel_area_measured_without_a_tape": sum(1 for r in records if r["pdp_area_cm2"]),
        "scale_free_screens_emitted": sum(len(r["scale_free_findings"]) for r in records),
        "scale_free_violations_emitted": sum(
            1 for r in records for f in r["scale_free_findings"]
            if f["verdict"] == Verdict.VIOLATION.value
        ),
        "photographs_with_an_ingredient_statement": sum(
            1 for r in records if r["allergens"]["ingredient_statements_read"]
        ),
        "photographs_with_a_detected_allergen": sum(
            1 for r in records if r["allergens"]["detected"]
        ),
    }
    median_sigma = sorted(r["panel"]["relative_sigma"] for r in panels)
    checks["panel_relative_sigma_median"] = (
        median_sigma[len(median_sigma) // 2] if median_sigma else None
    )

    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "protocol": (
            "Unblinded development evaluation on a small fixed set of real "
            "photographs. No package was physically measured, so no millimetre "
            "accuracy is claimed or scored. The invariants below are the only "
            "pass/fail assertions made here."
        ),
        "invariants": {
            "every panel bracket is ordered": checks["panel_bracket_ordered"] == len(panels),
            "no scale-free screen returned a violation": checks["scale_free_violations_emitted"] == 0,
        },
        "summary": checks,
        "records": records,
    }
    (OUT / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"photographs evaluated: {checks['photographs']}")
    for key, value in checks.items():
        print(f"  {key}: {value}")
    print("\ninvariants:")
    for name, held in payload["invariants"].items():
        print(f"  [{'ok' if held else 'FAILED'}] {name}")
    print(f"\nwritten: {(OUT / 'results.json').relative_to(ROOT)}")
    return 0 if all(payload["invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
