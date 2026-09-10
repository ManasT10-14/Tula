"""Score the allergen sweep against real ingredient panels read by eye first.

Run: python scripts/evaluate_ingredient_panels.py

The expectations come from `data/evaluation/ingredient-panels/annotations.v1.json`,
which was written from the photographs before this script existed. Three numbers
are reported and none of them is dressed up:

  * recovered   -- an annotated allergen the sweep reported, with the right kind
  * missed      -- an annotated allergen the sweep did not report
  * unexpected  -- an allergen the sweep reported that the annotation does not
                   have, including anything on a `must_not_report` list

Seven photographs is not an accuracy claim. It is enough to show whether the
deterministic lexicon does what it says on labels nobody wrote for it, and to
put the failures on the record next to the successes.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import Capture, analyse
from tula.domain.enums import Panel

SET = ROOT / "data/evaluation/ingredient-panels"
OUT = ROOT / "out" / "ingredient-panel-evaluation"


def score(image: dict, screen: dict, statements: int) -> dict:
    expected = {item["allergen"]: item["kind"] for item in image["expected_allergens"]}
    reported = {item["allergen"]: item["kind"] for item in screen.get("detected", [])}

    recovered = sorted(a for a in expected if a in reported and reported[a] == expected[a])
    wrong_kind = sorted(
        f"{a} (expected {expected[a]}, reported {reported[a]})"
        for a in expected if a in reported and reported[a] != expected[a]
    )
    missed = sorted(a for a in expected if a not in reported)
    unexpected = sorted(a for a in reported if a not in expected)
    forbidden = sorted(
        item["allergen"] for item in image.get("must_not_report", [])
        if item["allergen"] in reported
    )
    return {
        "file": image["file"],
        "language": image["language"],
        "ingredient_statements_read": statements,
        "expected": expected,
        "reported": reported,
        "recovered": recovered,
        "reported_with_the_wrong_kind": wrong_kind,
        "missed": missed,
        "unexpected": unexpected,
        "forbidden_matches": forbidden,
        "undeclared_flagged": screen.get("undeclared", []),
    }


def main() -> int:
    annotations = json.loads((SET / "annotations.v1.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    results = []
    for image in annotations["images"]:
        path = SET / image["file"]
        analysis = analyse([Capture(str(path), Panel.PDP)])
        intelligence = analysis.intelligence
        screen = intelligence.get("allergens", {}).get("screen", {})
        statements = len(intelligence.get("fields", {}).get("ingredients", []))
        record = score(image, screen, statements)
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["lines_read"] = analysis.scan.coverage.lines_read
        record["ingredient_text_read"] = [
            str(field.get("value", ""))[:400]
            for field in intelligence.get("fields", {}).get("ingredients", [])
        ]
        results.append(record)

    expected_total = sum(len(r["expected"]) for r in results)
    summary = {
        "photographs": len(results),
        "annotated_allergens": expected_total,
        "recovered": sum(len(r["recovered"]) for r in results),
        "reported_with_the_wrong_kind": sum(len(r["reported_with_the_wrong_kind"]) for r in results),
        "missed": sum(len(r["missed"]) for r in results),
        "unexpected": sum(len(r["unexpected"]) for r in results),
        "forbidden_matches": sum(len(r["forbidden_matches"]) for r in results),
        "photographs_with_an_ingredient_statement_read": sum(
            1 for r in results if r["ingredient_statements_read"]
        ),
        "negative_controls_clean": sum(
            1 for r in results if not r["expected"] and not r["reported"]
        ),
        "negative_controls": sum(1 for r in results if not r["expected"]),
    }

    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dataset": annotations["dataset_id"],
        "annotation_sha256": hashlib.sha256(
            (SET / "annotations.v1.json").read_bytes()
        ).hexdigest(),
        "protocol": annotations["protocol"],
        "summary": summary,
        "results": results,
    }
    (OUT / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for record in results:
        flag = "  " if not (record["missed"] or record["forbidden_matches"]) else "!!"
        print(f"{flag} {record['file']:30s} read={record['ingredient_statements_read']} "
              f"got={sorted(record['reported']) or '[]'} "
              f"missed={record['missed'] or '[]'} "
              f"unexpected={record['unexpected'] or '[]'}")
    print()
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"\nwritten: {(OUT / 'results.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
