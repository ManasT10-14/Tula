"""Does the allergen sweep survive a Hindi label and the whole real pipeline?

Run: python scripts/measure_hindi_allergens.py

The unit tests feed the lexicon text directly. This renders Devanagari labels,
photographs them the only way a script can -- by writing a PNG -- and puts them
through real recognition, real extraction and the real screen, because that is
where a Hindi label actually fails: not in the regex, but in whether anything
readable comes back at all.

These are generated labels with known content, not photographs of real Indian
packages. Their denominators must never be merged with the real-photograph sets
in `data/evaluation/`. What they establish is narrow and worth stating exactly:
that a Devanagari ingredient list, once recognised, is screened for the same
allergen categories an English one is.

Devanagari recognition comes from the optional Tesseract engine with the `hin`
language data. Without it the bundled RapidOCR model returns boxes for every
Indic glyph, and this script says so rather than reporting a silent zero.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import Capture, analyse
from tula.domain.enums import Panel
from tula.labgen import _font
from tula.ocr.auxiliary import tesseract_config

OUT = ROOT / "out" / "hindi-allergen-measurement"

# Each case: the printed rows, and the allergen categories a reader of Hindi
# would take from them. Written before the script was run.
CASES = [
    {
        "id": "hindi-biscuit",
        "rows": [
            "सूरज गोल्ड बिस्किट",
            "शुद्ध वजन 200 ग्राम",
            "अधिकतम खुदरा मूल्य रु 45.00 सभी करों सहित",
            "सामग्री: गेहूं का आटा, चीनी, दूध ठोस, काजू, नमक",
            "निर्माण तिथि 08/2026",
        ],
        "expected": {"gluten": "possible", "milk": "explicit", "tree nuts": "explicit"},
        "expected_undeclared": ["milk", "tree nuts"],
    },
    {
        "id": "hindi-with-contains",
        "rows": [
            "दक्कन नमकीन",
            "शुद्ध वजन 100 ग्राम",
            "सामग्री: बेसन, मूंगफली का तेल, नमक, मिर्च",
            "इसमें मूंगफली शामिल है।",
            "निर्माण तिथि 07/2026",
        ],
        "expected": {"peanuts": "explicit"},
        "expected_undeclared": [],
    },
    {
        "id": "hindi-cross-contact",
        "rows": [
            "गंगा चावल",
            "शुद्ध वजन 1 किलोग्राम",
            "सामग्री: चावल, नमक",
            "इसी कारखाने में बादाम का उपयोग होता है।",
            "निर्माण तिथि 06/2026",
        ],
        "expected": {"tree nuts": "cross_contact"},
        "expected_undeclared": [],
    },
    {
        "id": "hindi-free-from",
        "rows": [
            "शुद्ध आहार",
            "शुद्ध वजन 250 ग्राम",
            "सामग्री: ज्वार का आटा, नमक, ग्लूटेन रहित",
            "निर्माण तिथि 05/2026",
        ],
        "expected": {},
        "expected_undeclared": [],
    },
]


def render(case: dict, directory: Path) -> Path:
    width, row_height, size = 2400, 190, 104
    image = Image.new("RGB", (width, 120 + row_height * len(case["rows"])), "white")
    draw = ImageDraw.Draw(image)
    for index, row in enumerate(case["rows"]):
        draw.text((90, 60 + index * row_height), row, fill="#101418",
                  font=_font(index == 0, size, row))
    path = directory / f"{case['id']}.png"
    image.save(path)
    return path


def main() -> int:
    config = tesseract_config()
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="hindi-allergen-"))

    records = []
    for case in CASES:
        path = render(case, work)
        analysis = analyse([Capture(str(path), Panel.PDP)])
        screen = analysis.intelligence.get("allergens", {}).get("screen", {})
        reported = {item["allergen"]: item["kind"] for item in screen.get("detected", [])}
        expected = case["expected"]
        records.append({
            "id": case["id"],
            "printed": case["rows"],
            "ingredient_text_read": [
                str(f.get("value", ""))[:300]
                for f in analysis.intelligence.get("fields", {}).get("ingredients", [])
            ],
            "expected": expected,
            "reported": reported,
            "recovered": sorted(a for a in expected if reported.get(a) == expected[a]),
            "wrong_kind": sorted(f"{a} (expected {expected[a]}, got {reported[a]})"
                                 for a in expected if a in reported and reported[a] != expected[a]),
            "missed": sorted(a for a in expected if a not in reported),
            "unexpected": sorted(a for a in reported if a not in expected),
            "undeclared_expected": case["expected_undeclared"],
            "undeclared_reported": screen.get("undeclared", []),
        })

    total = sum(len(r["expected"]) for r in records)
    summary = {
        "labels": len(records),
        "annotated_allergens": total,
        "recovered": sum(len(r["recovered"]) for r in records),
        "wrong_kind": sum(len(r["wrong_kind"]) for r in records),
        "missed": sum(len(r["missed"]) for r in records),
        "unexpected": sum(len(r["unexpected"]) for r in records),
        "undeclared_lists_correct": sum(
            1 for r in records if r["undeclared_reported"] == r["undeclared_expected"]
        ),
        "ingredient_statement_read": sum(1 for r in records if r["ingredient_text_read"]),
    }
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "devanagari_recogniser": (
            f"tesseract {config[1]}" if config else
            "NONE -- Tesseract absent or disabled; Devanagari cannot be recognised"
        ),
        "protocol": (
            "Generated Devanagari labels with known printed content, run through "
            "real recognition, extraction and screening. Not photographs of real "
            "Indian packages; these denominators must not be merged with the "
            "real-photograph evaluation sets."
        ),
        "summary": summary,
        "records": records,
    }
    (OUT / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("Devanagari recogniser:", payload["devanagari_recogniser"])
    for record in records:
        flag = "  " if not (record["missed"] or record["unexpected"] or record["wrong_kind"]) else "!!"
        print(f"{flag} {record['id']:22s} got={sorted(record['reported']) or '[]'} "
              f"missed={record['missed'] or '[]'} unexpected={record['unexpected'] or '[]'}")
    print()
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"\nwritten: {(OUT / 'results.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
