"""Full OCR/extraction checks on retained dotted pixels and one price photo.

This is unblinded development verification, not a held-out accuracy estimate.
The generated labels test five known printed values; the photograph tests only
its visible front price, not a complete statutory MRP or a legal verdict.
Original images, annotations and earlier result folders are never modified.

python scripts/verify_retained_ocr.py --out out/retained-ocr-followup
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from evaluate_real_labels import accepted, fingerprint, overlaps, predictions, text_recovered

from tula.domain.enums import Panel
from tula.extract.pipeline import extract
from tula.ocr.base import OcrLine, OcrResult
from tula.ocr.engines import RapidOcrEngine

TARGETS = [
    {"field": "net_quantity", "value": {"value": 200., "unit": "g"}, "printed": "200 g", "bbox": [50, 130, 345, 190]},
    {"field": "retail_sale_price", "value": {"value": 180., "currency": "INR"}, "printed": "180.00", "bbox": [50, 215, 345, 270]},
    {"field": "manufacturing_date", "value": "2026-08", "printed": "08/2026", "bbox": [50, 300, 315, 355]},
    {"field": "expiry_date", "value": "2027-09", "printed": "09/2027", "bbox": [50, 385, 310, 440]},
    {"field": "batch_number", "value": "AB1234", "printed": "AB1234", "bbox": [50, 470, 355, 525]},
]


def correct_value(value, expected):
    if isinstance(expected, dict):
        return isinstance(value, dict) and all(value.get(key) == item for key, item in expected.items())
    return isinstance(value, str) and value.casefold() == expected.casefold()


def verify_fields(raw_ocr, raw_extraction, targets):
    found = predictions(raw_extraction)
    rows = []
    for target in targets:
        items = found.get(target["field"], [])

        def matches(item, *, candidates=False, expected=target):
            if not any(overlaps(box, expected["bbox"]) for box in item["boxes"]):
                return False
            values = [item["value"]]
            if candidates:
                for candidate in item.get("candidates", []):
                    values.extend([candidate, candidate.get("value") if isinstance(candidate, dict) else None])
            return any(correct_value(value, expected["value"]) for value in values)

        region = {"regions": [{"text": target["printed"], "bbox": target["bbox"]}]}
        rows.append({"field": target["field"], "expected": target,
                     "selected_literal": text_recovered(raw_ocr["lines"], region),
                     "candidate_literal": text_recovered(raw_ocr["lines"] + raw_ocr["candidates"], region),
                     "structured_exact": any(matches(item) for item in items),
                     "structured_candidate_exact": any(matches(item, candidates=True) for item in items),
                     "accepted_exact": any(accepted(item) and matches(item) for item in items),
                     "incorrect_accepted": any(accepted(item) and not matches(item) for item in items),
                     "predictions": items})
    return rows


def extract_record(ocr, path, targets):
    for line in ocr.lines:
        line.frame = str(path)
    result = extract([(Panel.PDP, ocr)])
    raw = {"declarations": {key.value: value.model_dump(mode="json") for key, value in result.declarations.items()},
           "intelligence": result.intelligence, "gtin_candidates": result.gtin_candidates}
    return raw, verify_fields(asdict(ocr), raw, targets)


def totals(rows):
    return {"denominator": len(rows), **{name: sum(row[name] for row in rows) for name in (
        "selected_literal", "candidate_literal", "structured_exact", "structured_candidate_exact",
        "accepted_exact", "incorrect_accepted")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("Use a new output directory; earlier evaluation evidence is retained")
    args.out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    before = fingerprint(root)
    before["source_sha256"]["scripts/verify_retained_ocr.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    saved = json.loads((root / "out/price-ocr-improvements/retained-dotted-results.json").read_text(encoding="utf-8"))
    baseline = {item["name"]: item["ocr"] for item in saved["results"]}
    expected_hashes = {item["name"]: item["sha256"] for item in saved["results"]}
    previous_price = json.loads((root / "out/real-label-evaluation-after-context/IN-front-8901719134852.json").read_text(encoding="utf-8"))
    baseline["parle_price"] = previous_price["ocr"]
    expected_hashes["parle_price"] = previous_price["image_sha256"]
    cases = [(name, root / "out/difficult-ocr-verified/images" / f"{name}.png", TARGETS)
             for name in ("dot_matrix_mrp_dates", "severely_sparse_dot_matrix", "clean")]
    cases.append(("parle_price", root / previous_price["source_path"], [
        {"field": "retail_sale_price", "value": {"value": 10., "currency": "INR"},
         "printed": "10", "bbox": [1830, 131, 2130, 321]}]))
    engine = RapidOcrEngine()
    records = []
    for name, path, targets in cases:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in expected_hashes and digest != expected_hashes[name]:
            raise ValueError(f"Retained image bytes changed: {name}")
        tick = time.perf_counter()
        ocr = engine.read(str(path))
        extraction, rows = extract_record(ocr, path, targets)
        record = {"name": name, "path": str(path), "image_sha256": digest,
                  "elapsed_ms": round((time.perf_counter() - tick) * 1000),
                  "ocr": asdict(ocr), "extraction": extraction, "fields": rows, "totals": totals(rows)}
        if name in baseline:
            old = baseline[name]
            restored = OcrResult(**{**old, "lines": [OcrLine(**line) for line in old["lines"]]})
            _, replay = extract_record(restored, path, targets)
            record["baseline_ocr_replayed_with_current_extraction"] = {"fields": replay, "totals": totals(replay)}
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        records.append(record)
        print(name, json.dumps(record["totals"]), flush=True)
    after = fingerprint(root)
    after["source_sha256"]["scripts/verify_retained_ocr.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    changed = [key for key, value in before["source_sha256"].items() if after["source_sha256"].get(key) != value]
    output = {"limitations": __doc__, "baseline_comparison": "Retained baseline OCR replayed through the same current extraction code isolates OCR changes; it is not a fresh run of the old recognizer.",
              "environment": before, "environment_after": after, "source_changed_during_run": changed, "results": records}
    (args.out / "results.json").write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
