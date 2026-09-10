"""Score declaration extraction on packages whose labels point at their values.

Run: python scripts/evaluate_arrow_layouts.py [--out DIR]

Indian flexible packaging routinely prints the mandatory declarations as a
column of labels joined to a column of values by arrows, and the value column is
inkjet-coded separately from the pre-printed labels -- so it drifts. Following an
arrow then lands on the next field's value, and a recogniser that merges a label
with whatever sits to its right produces confidently wrong pairs.

Three outcomes are scored separately, because they are not equally bad:

  correct    the declared value was recovered and may feed a finding
  candidate  the declared value was recovered but held for review -- the officer
             is shown the right number and asked to confirm it
  abstain    nothing usable was recovered
  WRONG      a value was reported and it is not the one on the package

A wrong reading is the only true failure. An abstention costs an officer a
correction; a wrong reading costs a wrongly cleared or wrongly accused package.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Lane, Panel

SET = ROOT / "data" / "evaluation" / "arrow-layouts"
IMAGES = SET / "images"
DEFAULT_OUT = ROOT / "out" / "arrow-layout-evaluation"

CHECKS = {
    "net_quantity": (DC.NET_QUANTITY, ("value", "unit")),
    "retail_sale_price": (DC.RETAIL_SALE_PRICE, ("value",)),
    # `per_value` is the parser's *denominator* -- the 100 in "per 100 g" -- and
    # `value` is the money. "0.34 per g" is value 0.34, per_value 1.
    "unit_sale_price": (DC.UNIT_SALE_PRICE, ("value", "per_unit")),
    "date_of_packing": (DC.DATE_OF_PACKING, ("year", "month")),
    "generic_name": (DC.GENERIC_NAME, ("contains",)),
    "country_of_origin": (DC.COUNTRY_OF_ORIGIN, ("country",)),
}


def _close(got, want) -> bool:
    if isinstance(want, float) and isinstance(got, (int, float)):
        return abs(float(got) - want) <= max(0.01, abs(want) * 0.005)
    if isinstance(want, str):
        return isinstance(got, str) and want.casefold() in got.casefold()
    return got == want


def score_one(declarations, field, expected):
    """correct | abstain | wrong, plus what was actually reported."""
    klass, keys = CHECKS[field]
    declaration = declarations.get(klass)
    if declaration is None:
        return "abstain", None
    norm = declaration.norm
    if norm.get("requires_review"):
        # A right answer held for review is worth far more to an inspector than
        # silence, so score it apart from a genuine abstention.
        raw = (declaration.raw or "").casefold().replace(" ", "")
        wanted = []
        for key in keys:
            want = expected.get(key)
            if isinstance(want, float):
                wanted.append(f"{want:g}")
            elif isinstance(want, str):
                wanted.append(want.casefold())
        hit = bool(wanted) and all(w in raw for w in wanted)
        return ("candidate" if hit else "abstain"), {"held_for_review": declaration.raw}
    if field == "generic_name":
        got = declaration.raw or ""
        return ("correct" if _close(got, expected["contains"]) else "wrong"), got
    reported = {k: norm.get(k) for k in keys}
    if all(reported[k] is None for k in keys):
        return "abstain", reported
    return (("correct" if all(_close(reported[k], expected[k]) for k in keys)
             else "wrong"), reported)


def run(package, out_dir):
    frames = [Capture(str(IMAGES / package["declarations_frame"]), Panel.BACK)]
    scenarios = {
        "declarations_panel_only": frames,
        "all_faces": [Capture(str(IMAGES / n),
                              Panel.PDP if n.endswith("-2.png") else Panel.BACK)
                      for n in package["all_frames"]],
    }
    record = {"id": package["id"], "note": package["note"], "scenarios": {}}
    for name, captures in scenarios.items():
        analysis = analyse(captures, AnalyseOptions(lane=Lane.FIELD, engine_name="rapidocr"))
        results, tally = {}, {"correct": 0, "candidate": 0, "abstain": 0, "wrong": 0}
        for field, expected in package["truth"].items():
            if field not in CHECKS:
                continue
            verdict, reported = score_one(analysis.declarations, field, expected)
            tally[verdict] += 1
            results[field] = {"outcome": verdict, "expected": expected, "reported": reported}
        gtin = package["truth"].get("gtin")
        if gtin:
            found = gtin in (analysis.scan.notes if hasattr(analysis.scan, "notes") else [])
            results["gtin"] = {"outcome": "correct" if gtin else "abstain",
                               "expected": gtin, "reported": None if not found else gtin}
        record["scenarios"][name] = {"tally": tally, "fields": results}
        print(f"  {name:28s} correct={tally['correct']} "
              f"candidate={tally['candidate']} abstain={tally['abstain']} "
              f"WRONG={tally['wrong']}")
        for field, item in results.items():
            if item["outcome"] == "wrong":
                print(f"      !! {field}: expected {item['expected']} got {item['reported']}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    spec = json.loads((SET / "annotations.v1.json").read_text(encoding="utf-8"))
    records = []
    for package in spec["packages"]:
        print(f"\n{package['id']}")
        records.append(run(package, args.out))

    totals = {"correct": 0, "candidate": 0, "abstain": 0, "wrong": 0}
    for record in records:
        for scenario in record["scenarios"].values():
            for key in totals:
                totals[key] += scenario["tally"][key]
    payload = {"generated": datetime.now(UTC).isoformat(timespec="seconds"),
               "python": platform.python_version(), "platform": platform.platform(),
               "protocol": spec["protocol"], "totals": totals, "packages": records}
    (args.out / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nTOTAL  correct={totals['correct']}  abstain={totals['abstain']}  "
          f"WRONG={totals['wrong']}")
    print("written:", args.out / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
