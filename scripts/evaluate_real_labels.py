"""Evaluate actual OCR/extraction on retained, agent-created real-photo labels.

Run: python scripts/evaluate_real_labels.py
This is an unblinded development evaluation, not independent accuracy evidence.
The script deliberately does not call a legal or medical verdict engine, load
capture metadata/fixture sidecars, or infer text from a filename or catalogue.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = ROOT / "data/evaluation/real-labels.v1.json"
UNKNOWN_STATES = {"not_visible", "illegible", "illegible_or_outside_frame", "occluded_or_cropped"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normal_text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def exact_value(actual, expected):
    """No fuzzy substitutions, decimal stripping or date-order assumptions."""
    if actual is None or isinstance(actual, bool):
        return False
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    return re.sub(r"\s", "", normal_text(actual)) == re.sub(r"\s", "", normal_text(expected))


def text_contains(actual, printed):
    """Exact printed span; 20 cannot match 200, 20.5, or a component of a date."""
    target = r"\s*".join(re.escape(word) for word in normal_text(printed).split())
    if re.fullmatch(r"\d+(?:\.\d+)?", normal_text(printed)):
        # A rupee 10/- suffix is allowed; 10/2026 and 10.00 are different text.
        pattern = r"(?<![\w./-])" + target + r"(?![\w.]|[/-]\d)"
    else:
        pattern = r"(?<!\w)" + target + r"(?!\w)"
    return bool(re.search(pattern, normal_text(actual)))


def overlaps(left, right):
    if not left or not right or len(left) != 4 or len(right) != 4:
        return False
    x0, y0, x1, y1 = left
    u0, v0, u1, v1 = right
    intersection = max(0, min(x1, u1) - max(x0, u0)) * max(0, min(y1, v1) - max(y0, v0))
    smaller = min(max(0, x1 - x0) * max(0, y1 - y0), max(0, u1 - u0) * max(0, v1 - v0))
    return smaller > 0 and intersection / smaller >= 0.25


def load_annotations(path, root=ROOT):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("images"):
        raise ValueError("Unsupported or empty annotation dataset")
    hashes, ids = set(), set()
    for record in data["images"]:
        image_path = (root / record["path"]).resolve()
        if not image_path.is_relative_to(root.resolve()):
            raise ValueError("Annotation image path must stay inside the repository")
        if record["sha256"] in hashes or record["id"] in ids:
            raise ValueError("Duplicate image hash or identifier would double-count the dataset")
        hashes.add(record["sha256"])
        ids.add(record["id"])
        if sha256(image_path) != record["sha256"]:
            raise ValueError(f"Image SHA-256 mismatch: {record['id']}")
        with Image.open(image_path) as image:
            if list(image.size) != record["size"] or image.getexif().get(274, 1) != 1:
                raise ValueError(f"Image dimensions/orientation differ from annotation: {record['id']}")
        if record["unknown_default"]["status"] not in UNKNOWN_STATES:
            raise ValueError("Unknown annotations must not assert package-wide absence")
        seen = set()
        for field in record["fields"]:
            if field["field"] not in data["target_fields"] or field["field"] in seen:
                raise ValueError("Unknown or duplicate field annotation")
            seen.add(field["field"])
            if field["status"] != "readable":
                if field["status"] not in UNKNOWN_STATES:
                    raise ValueError("Unknown annotation status")
                continue
            if not field.get("expected_values") or not field.get("regions"):
                raise ValueError("Readable fields need independently transcribed values and boxes")
            width, height = record["size"]
            for region in field["regions"]:
                x0, y0, x1, y1 = region["bbox"]
                if not region["text"] or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                    raise ValueError("Invalid annotation text or original-coordinate box")
    return data


def inventory(root):
    groups = defaultdict(list)
    for path in sorted((root / "data/uploads").glob("*/*.jpg")):
        groups[sha256(path)].append(path.relative_to(root).as_posix())
    return dict(groups)


def predictions(extracted):
    """Keep structured primary values and supplementary review items distinct."""
    out = defaultdict(list)
    for name, declaration in extracted["declarations"].items():
        value = declaration.get("norm", {})
        provenance = declaration.get("provenance") or {}
        sources = provenance.get("sources") or value.get("source_spans", [])
        boxes = [s["bbox"] for s in sources if s.get("bbox")]
        if declaration.get("bbox"):
            boxes.append(declaration["bbox"])
        out[name].append({"origin": "declaration", "value": value,
                          "raw": declaration.get("raw"), "boxes": boxes,
                          "status": provenance.get("status") or (
                              "cue_only" if not value else "needs_review" if value.get("requires_review") else "detected"),
                          "confidence": provenance.get("ocr_confidence", declaration.get("confidence", 0)),
                          "candidates": value.get("candidates", [])})
    for name, items in extracted.get("intelligence", {}).get("fields", {}).items():
        for item in items:
            out[name].append({"origin": "supplementary", "value": item.get("value"),
                              "raw": item.get("raw"), "status": item.get("status"),
                              "confidence": item.get("ocr_confidence", 0),
                              "boxes": [s["bbox"] for s in item.get("sources", []) if s.get("bbox")],
                              "candidates": item.get("candidates", [])})
    for gtin in extracted.get("gtin_candidates", []):
        out["gtin"].append({"origin": "ocr_digits", "value": gtin, "raw": gtin,
                            "status": "candidate", "confidence": None, "boxes": [], "candidates": []})
    return dict(out)


def _at(value, path):
    if not path:
        return value
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def accepted(item):
    return (item.get("status") in {"detected", "relative_duration"}
            and (item.get("confidence") or 0) >= 0.55 and bool(item.get("value")))


def prediction_matches(item, annotation, *, alternatives=False):
    values = [item.get("value")]
    if alternatives:
        # A numeric candidate may be the value dictionary itself, or wrapped
        # as {value: {value: 20, currency: INR}, ...} in supplementary fields.
        for candidate in item.get("candidates", []):
            values.append(candidate)
            if isinstance(candidate, dict):
                values.append(candidate.get("value"))
    return any(exact_value(_at(value, annotation.get("value_path")), expected)
               and any(exact_value(region["text"], expected)
                       and overlaps(box, region["bbox"])
                       for box in item.get("boxes", []) for region in annotation["regions"])
               for value in values for expected in annotation["expected_values"])


def text_recovered(items, annotation):
    """Join only boxes in one annotated region from the same OCR pass."""
    for region in annotation["regions"]:
        local = [item for item in items if overlaps(item.get("bbox"), region["bbox"])]
        if any(text_contains(item.get("text", ""), region["text"]) for item in local):
            return True
        groups = defaultdict(list)
        for item in local:
            groups[item.get("variant", "selected")].append(item)
        for lines in groups.values():
            if len(lines) < 2:
                continue
            # No text from other faces, unrelated regions or separate passes
            # is combined into an invented candidate.
            ordered = sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))
            if text_contains(" ".join(line.get("text", "") for line in ordered), region["text"]):
                return True
    return False


def score_image(record, extracted, ocr, target_fields):
    found = predictions(extracted)
    annotated = {field["field"]: field for field in record["fields"]}
    rows = []
    for name in target_fields:
        annotation = annotated.get(name, {"field": name, **record["unknown_default"]})
        items = found.get(name, [])
        row = {"field": name, "annotation_status": annotation["status"], "predictions": items}
        if annotation["status"] != "readable":
            row.update(scored=False, reason=annotation.get("note", annotation.get("reason")),
                       unknown_with_output=bool(items))
            rows.append(row)
            continue
        matches = [prediction_matches(item, annotation) for item in items]
        confident = [item for item in items if accepted(item)]
        exact_accepted = any(accepted(item) and match for item, match in zip(items, matches))
        wrong_accepted = any(accepted(item) and not match for item, match in zip(items, matches))
        chosen_ocr = text_recovered(ocr.get("lines", []), annotation)
        candidate_ocr = text_recovered(ocr.get("candidates", []) + ocr.get("lines", []), annotation)
        if wrong_accepted:
            outcome = "incorrect_accepted"
        elif exact_accepted:
            outcome = "exact_accepted"
        elif any(matches):
            outcome = "exact_held_for_review"
        elif items:
            outcome = "held_for_review"
        else:
            outcome = "no_structured_prediction"
        failure = None
        if not any(matches):
            failure = ("extraction_classification_or_normalization" if chosen_ocr else
                       "candidate_selection_or_review_filtering" if candidate_ocr else
                       "ocr_missed_or_misread")
        row.update(scored=True, expected_values=annotation["expected_values"],
                   regions=annotation["regions"], outcome=outcome, failure_stage=failure,
                   selected_exact=any(matches), accepted_exact=exact_accepted,
                   incorrect_accepted=wrong_accepted, abstained=not confident,
                   structured_candidate_recovery=any(prediction_matches(item, annotation, alternatives=True)
                                                     for item in items),
                   ocr_selected_recovery=chosen_ocr, ocr_candidate_recovery=candidate_ocr)
        rows.append(row)
    return rows


METRICS = ("selected_exact", "accepted_exact", "structured_candidate_recovery", "ocr_selected_recovery",
           "ocr_candidate_recovery", "abstained", "incorrect_accepted")


def aggregate(records, target_fields):
    by_field = {name: {"readable": 0, "unknown": 0, "unknown_with_output": 0,
                       **{metric: 0 for metric in METRICS}} for name in target_fields}
    failures = Counter()
    errors = []
    for record in records:
        if record.get("error"):
            errors.append({"id": record["id"], "error": record["error"]})
            continue
        for row in record["fields"]:
            count = by_field[row["field"]]
            if not row["scored"]:
                count["unknown"] += 1
                count["unknown_with_output"] += int(row["unknown_with_output"])
                continue
            count["readable"] += 1
            for metric in METRICS:
                count[metric] += int(row[metric])
            if row["failure_stage"]:
                failures[row["failure_stage"]] += 1
    totals = {key: sum(count[key] for count in by_field.values()) for key in next(iter(by_field.values()))}
    return {"totals": totals, "by_field": by_field, "failure_stages": dict(failures),
            "runtime_errors": errors, "completed_images": len(records) - len(errors),
            "unevaluable_fields": [name for name, count in by_field.items() if not count["readable"]]}


def fingerprint(root):
    paths = [*(root / "src/tula/ocr").glob("*.py"), *(root / "src/tula/extract").glob("*.py"),
             *(root / "src/tula/domain").glob("*.py"),
             root / "src/tula/imaging/quality.py", Path(__file__)]
    packages, models = {}, {}
    for name in ("rapidocr-onnxruntime", "rapidocr", "onnxruntime", "opencv-python", "Pillow", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
            if name.startswith("rapidocr"):
                distribution = importlib.metadata.distribution(name)
                for item in distribution.files or []:
                    if Path(str(item)).suffix in {".onnx", ".yaml"}:
                        models[str(item)] = sha256(distribution.locate_file(item))
        except importlib.metadata.PackageNotFoundError:
            pass
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": packages,
            "rapidocr_model_and_config_sha256": models,
            "source_sha256": {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths)},
            "ocr_environment": {key: os.environ.get(key) for key in
                                ("TULA_OCR", "TULA_OCR_MAX_PASSES", "TULA_OCR_BUDGET_SECONDS",
                                 "TULA_TESSERACT", "TULA_TESSERACT_LANGS", "TESSDATA_PREFIX")}}


def markdown_report(result):
    summary = result["summary"]
    lines = ["# Real package photo extraction evaluation", "", result["limitations"], "",
             f"Dataset: **{result['dataset_id']}**; annotation SHA-256 `{result['annotation_sha256']}`.", "",
             (f"{summary['completed_images']}/{len(result['images'])} distinct annotated photos completed. "
             f"{result['inventory']['upload_copies']} uploads contain "
             f"{result['inventory']['distinct_hashes']} unique image hashes. Each annotated hash is scored once."), "",
             ("The annotations test printed brand/sub-brand identity, commodity tokens and visible rupee "
             "amounts. Unknown fields are excluded; they are not scored as correctly absent."), "",
             "| Field | Readable | Accepted exact | Any selected exact | Structured candidate | OCR selected | OCR any candidate | Abstained | Wrong accepted | Unknown |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in summary["by_field"].items():
        lines.append(f"| {name} | {row['readable']} | {row['accepted_exact']} | {row['selected_exact']} | "
                     f"{row['structured_candidate_recovery']} | {row['ocr_selected_recovery']} | "
                     f"{row['ocr_candidate_recovery']} | {row['abstained']} | "
                     f"{row['incorrect_accepted']} | {row['unknown']} |")
    lines += ["", ("All score columns are counts out of Readable, not independently validated accuracy rates. "
              "Abstained means no confident structured value was emitted for that field; it includes "
              "missing output and values held for review. OCR recovery does not prove field assignment. "
              "Wrong accepted counts a confident nonmatching value or source, even if another value matched."), "",
              "No readable positive examples were available for: " + ", ".join(summary["unevaluable_fields"]) + ".", "",
              "## Every readable target", "",
              "| Photo | Field | Expected | Outcome | Failure stage |",
              "|---|---|---|---|---|"]
    for record in result["images"]:
        for row in record.get("fields", []):
            if row["scored"]:
                expected = "; ".join(str(value) for value in row["expected_values"])
                lines.append(f"| {record['id']} | {row['field']} | {expected} | {row['outcome']} | "
                             f"{row['failure_stage'] or '—'} |")
    lines += ["", "## Outputs on unknown targets (unscored)", "",
              ("These observations need source review. They are not assigned correctness against hidden, "
              "blurred or cropped text and do not establish a missing declaration."), ""]
    for record in result["images"]:
        for row in record.get("fields", []):
            if not row["scored"] and row["predictions"]:
                raws = list(dict.fromkeys(str(item.get("raw", "")) for item in row["predictions"]))
                lines.append(f"- {record['id']} / {row['field']} ({row['annotation_status']}): "
                             + json.dumps(raws, ensure_ascii=False))
    if summary["runtime_errors"]:
        lines += ["", "Runtime errors were excluded from score denominators; this run is incomplete.",
                  json.dumps(summary["runtime_errors"], ensure_ascii=False)]
    if result.get("source_changed_during_run"):
        lines += ["", "INCOMPLETE: source changed during this run: "
                  + ", ".join(result["source_changed_during_run"])]
    lines += ["", ("Full OCR boxes, alternatives, quality measurements, extracted fields, environment and "
              "source hashes are retained in results.json and per-image JSON files. "
              "No OCR or extraction pipeline modifications were made by this evaluation script."), ""]
    return "\n".join(lines)


def run(annotation_path, output, root=ROOT):
    from tula.domain.enums import Panel
    from tula.extract.pipeline import extract
    from tula.ocr.engines import RapidOcrEngine

    data = load_annotations(annotation_path, root)
    output.mkdir(parents=True, exist_ok=True)
    groups = inventory(root)
    result = {"dataset_id": data["dataset_id"], "annotation_sha256": sha256(annotation_path),
              "started_at": datetime.now(UTC).isoformat(), "engine": "rapidocr",
              "limitations": data["protocol"] + " " + data["scope"],
              "inventory": {"upload_copies": sum(map(len, groups.values())), "distinct_hashes": len(groups),
                            "unannotated_hashes": sorted(set(groups) - {r["sha256"] for r in data["images"]}),
                            "duplicate_paths": groups},
              "environment": fingerprint(root), "images": []}
    for relative in result["environment"]["source_sha256"]:
        snapshot = output / "evaluated-source" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((root / relative).read_bytes())
    (output / "annotations.json").write_bytes(Path(annotation_path).read_bytes())
    engine = RapidOcrEngine()  # Explicit real CPU OCR; never use get_engine/env fixture selection.
    for record in data["images"]:
        started = time.perf_counter()
        row = {"id": record["id"], "image_sha256": record["sha256"], "source_path": record["path"]}
        try:
            path = root / record["path"]
            ocr = engine.read(str(path))
            if ocr.engine != "rapidocr":
                raise RuntimeError("Real-photo evaluation requires the actual RapidOCR engine")
            for line in ocr.lines:
                line.frame = record["path"]
            extracted = extract([(Panel(record["panel"]), ocr)])
            # Enum keys need explicit serialization; this is extraction only.
            raw_extraction = {"declarations": {key.value: value.model_dump(mode="json")
                                               for key, value in extracted.declarations.items()},
                              "intelligence": extracted.intelligence,
                              "gtin_candidates": extracted.gtin_candidates,
                              "warnings": extracted.warnings, "full_text": extracted.full_text}
            raw_ocr = asdict(ocr)
            row["fields"] = score_image(record, raw_extraction, raw_ocr, data["target_fields"])
            row["ocr"] = raw_ocr
            row["extraction"] = raw_extraction
        except Exception as exc:  # noqa: BLE001 - retain failed frames; CLI fails on runtime errors.
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        result["images"].append(row)
        (output / f"{record['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{record['id']}: {row.get('error', 'completed')} ({row['elapsed_ms']} ms)", flush=True)
    result["completed_at"] = datetime.now(UTC).isoformat()
    result["environment_after"] = fingerprint(root)
    before, after = result["environment"]["source_sha256"], result["environment_after"]["source_sha256"]
    result["source_changed_during_run"] = [key for key in set(before) | set(after) if before.get(key) != after.get(key)]
    result["summary"] = aggregate(result["images"], data["target_fields"])
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(markdown_report(result), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "out/real-label-evaluation")
    args = parser.parse_args()
    result = run(args.annotations, args.out)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 1 if result["summary"]["runtime_errors"] or result["source_changed_during_run"] else 0


if __name__ == "__main__":
    sys.exit(main())
