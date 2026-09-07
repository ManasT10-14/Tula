"""Evaluate frozen visual-first annotations using real OCR on unchanged originals.

This scores literal recovery separately from explicitly typed extraction targets.
It never supplies annotation text, country, filenames or source captions to OCR.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

try:
    from scripts.evaluate_real_labels import exact_value, fingerprint, overlaps, sha256
except ModuleNotFoundError:  # Direct python scripts/evaluate_additional_labels.py invocation.
    from evaluate_real_labels import exact_value, fingerprint, overlaps, sha256

ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = ROOT / "data/evaluation/additional-real-labels/annotations.v1.json"
METRICS = ("selected_exact", "candidate_exact", "accepted_exact", "exact_held_for_review",
           "wrong_accepted", "abstained")


def load_annotations(path, root=ROOT):
    path, root = Path(path), Path(root).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    frozen = json.loads(path.with_name("FROZEN.json").read_text(encoding="utf-8"))
    if sha256(path) != frozen["annotation_sha256"]:
        raise ValueError("Frozen annotation SHA-256 mismatch")
    if data.get("schema_version") != 1 or not data.get("images"):
        raise ValueError("Unsupported or empty dataset")
    hashes, ids, targets = set(), set(), set()
    counts = Counter()
    for record in data["images"]:
        image_path = (root / record["path"]).resolve()
        if not image_path.is_relative_to(root):
            raise ValueError("Image path escapes repository")
        if record["sha256"] in hashes or record["id"] in ids:
            raise ValueError("Duplicate image hash or identifier")
        if not re.fullmatch(r"[a-z0-9-]+", record["id"]):
            raise ValueError("Unsafe image identifier")
        hashes.add(record["sha256"])
        ids.add(record["id"])
        if sha256(image_path) != record["sha256"]:
            raise ValueError("Original photo SHA-256 mismatch")
        with Image.open(image_path) as image:
            if list(image.size) != record["size"] or image.getexif().get(274, 1) != 1:
                raise ValueError("Image dimensions or orientation differ from annotation")
        width, height = record["size"]
        for target in record["targets"]:
            if target["id"] in targets:
                raise ValueError("Duplicate target identifier")
            targets.add(target["id"])
            x0, y0, x1, y1 = target["bbox"]
            if not target["literal"] or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError("Invalid original-coordinate target")
            counts["literal_targets"] += 1
            if target["structured_scored"]:
                if not target.get("structured_field") or not target.get("expected"):
                    raise ValueError("Structured target needs explicit field and expectation")
                counts["structured_targets"] += 1
    if any(counts[key] != frozen[key] for key in counts):
        raise ValueError("Frozen target count mismatch")
    return data


def contains_literal(actual, expected):
    """Whitespace/case only: punctuation, every digit and token boundaries matter."""
    actual = unicodedata.normalize("NFKC", str(actual)).casefold()
    expected = re.sub(r"\s", "", unicodedata.normalize("NFKC", expected).casefold())
    if not expected:
        return False
    pattern = r"\s*".join(re.escape(char) for char in expected)
    return bool(re.search(r"(?<!\w)" + pattern + r"(?!\w)", actual))


def literal_evidence(items, target, frame, *, combine=False):
    local = [item for item in items if item.get("frame") == frame
             and overlaps(item.get("bbox"), target["bbox"])]
    found = [[item] for item in local if contains_literal(item.get("text", ""), target["literal"])]
    if combine:
        groups = defaultdict(list)
        for item in local:
            if item.get("variant"):
                groups[item["variant"]].append(item)
        for lines in groups.values():
            ordered = sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0]))
            if len(ordered) > 1 and contains_literal(" ".join(item["text"] for item in ordered), target["literal"]):
                found.append(ordered)
    return found


def predictions(extracted):
    found = defaultdict(list)
    for name, declaration in extracted.get("declarations", {}).items():
        value = declaration.get("norm") or {}
        provenance = declaration.get("provenance") or {}
        found[name].append({"origin": "declaration", "value": value,
            "raw": declaration.get("raw"), "status": provenance.get("status"),
            "ocr_confidence": provenance.get("ocr_confidence"),
            "extraction_confidence": provenance.get("extraction_confidence"),
            "method": provenance.get("method"), "sources": provenance.get("sources", []),
            "candidates": value.get("candidates", []), "provenance": provenance})
    for name, items in extracted.get("intelligence", {}).get("fields", {}).items():
        found[name].extend({**item, "origin": "supplementary"} for item in items)
    return dict(found)


def accepted(item):
    value = item.get("value")
    score = item.get("ocr_confidence")
    return (item.get("status") in {"detected", "relative_duration"} and bool(value)
            and type(score) in {float, int} and math.isfinite(score) and score >= .55
            and not item.get("requires_review")
            and not (isinstance(value, dict) and
                     (value.get("requires_review") or value.get("currency_verified") is False)))


def normalized_value(value, field):
    """Decode emitted fields only; never infer currency from annotation or country."""
    if field == "batch_number":
        return value if isinstance(value, dict) else {"value": value}
    if not isinstance(value, dict):
        return {}
    result = dict(value)
    if field == "unit_sale_price":
        result["basis_value"] = value.get("basis_value", value.get("per_value"))
        result["basis_unit"] = value.get("basis_unit", value.get("per_unit"))
        if not result.get("currency") and re.match(r"^\s*(?:₹|rs\.?|inr)\s*\d", str(value.get("raw", "")), re.IGNORECASE):
            result["currency"] = "INR"
            result["currency_basis"] = "recognized unit-price raw token"
    return result


def matches(item, target, frame, *, alternatives=False):
    if not any(source.get("frame") == frame and overlaps(source.get("bbox"), target["bbox"])
               for source in item.get("sources", [])):
        return False
    values = [item.get("value")]
    if alternatives:
        for candidate in item.get("candidates", []):
            values.append(candidate)
            if isinstance(candidate, dict):
                values.append(candidate.get("value"))
    for value in values:
        normalized = normalized_value(value, target["structured_field"])
        if all(exact_value(normalized.get(key), expected) for key, expected in target["expected"].items()):
            return True
    return False


def score_image(record, extracted, ocr):
    found, rows = predictions(extracted), []
    for target in record["targets"]:
        selected = literal_evidence(ocr.get("lines", []), target, record["path"])
        candidates = literal_evidence(ocr.get("candidates", []), target, record["path"], combine=True)
        row = {"target": target, "literal_selected": bool(selected),
               "literal_candidate": bool(selected or candidates),
               "literal_evidence": {"selected": selected, "candidates": candidates}}
        if target["structured_scored"]:
            items = found.get(target["structured_field"], [])
            exact = [matches(item, target, record["path"]) for item in items]
            good = any(accepted(item) and match for item, match in zip(items, exact))
            wrong = any(accepted(item) and not match for item, match in zip(items, exact))
            candidate_exact = any(matches(item, target, record["path"], alternatives=True) for item in items)
            row.update(predictions=items, selected_exact=any(exact), candidate_exact=candidate_exact,
                accepted_exact=good, exact_held_for_review=any(exact) and not good and not wrong,
                wrong_accepted=wrong, abstained=not any(accepted(item) for item in items))
            row["outcome"] = ("wrong_accepted" if wrong else "accepted_exact" if good else
                              "exact_held_for_review" if any(exact) else "candidate_only" if candidate_exact
                              else "held_nonmatching" if items else "no_structured_prediction")
            row["failure_stage"] = ("incorrect_accepted_value_or_source" if wrong else
                "held_for_review" if any(exact) and not good else None if good else
                "extraction_classification_or_normalization" if selected else
                "candidate_selection_or_review_filtering" if candidates else "ocr_missed_or_misread")
        rows.append(row)
    scored = {target["structured_field"] for target in record["targets"] if target["structured_scored"]}
    return rows, {name: items for name, items in found.items() if name not in scored}


def summarize(data, records):
    summary = {"photos": len(data["images"]), "completed_photos": 0, "literal_targets": 0,
               "structured_targets": 0, "literal_selected": 0, "literal_candidate": 0,
               **dict.fromkeys(METRICS, 0), "by_field": {}, "runtime_errors": []}
    for photo in data["images"]:
        summary["literal_targets"] += len(photo["targets"])
        summary["structured_targets"] += sum(target["structured_scored"] for target in photo["targets"])
        for target in photo["targets"]:
            if target["structured_scored"]:
                summary["by_field"].setdefault(target["structured_field"], {"targets": 0, **dict.fromkeys(METRICS, 0)})["targets"] += 1
    for record in records:
        if record.get("error"):
            summary["runtime_errors"].append({"id": record["id"], "error": record["error"]})
            continue
        summary["completed_photos"] += 1
        for row in record["scores"]:
            for metric in ("literal_selected", "literal_candidate"):
                summary[metric] += int(row[metric])
            if row["target"]["structured_scored"]:
                for metric in METRICS:
                    summary[metric] += int(row[metric])
                    summary["by_field"][row["target"]["structured_field"]][metric] += int(row[metric])
    return summary


def environment(root):
    from tula.ocr.auxiliary import tesseract_config

    result = fingerprint(root)
    result["source_sha256"][Path(__file__).relative_to(root).as_posix()] = sha256(__file__)
    config = tesseract_config()
    result["tesseract"] = {"available": bool(config)}
    if config:
        executable, languages, missing = config
        def command(arg):
            return subprocess.run([executable, arg], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        listing = command("--list-langs")
        directory = re.search(r'"([^"]+)"', listing)
        tessdata = Path(directory[1]) if directory else Path(os.getenv("TESSDATA_PREFIX", Path(executable).parent / "tessdata"))
        result["tesseract"] = {"available": True, "executable": executable,
            "executable_sha256": sha256(executable), "version": command("--version"),
            "languages": languages, "missing_languages": missing,
            "traineddata_sha256": {language: sha256(tessdata / f"{language}.traineddata")
                if (tessdata / f"{language}.traineddata").is_file() else None
                for language in sorted(set(languages.split("+")) | {"osd"})}}
    return result


def report(result):
    summary = result["summary"]
    lines = ["# Four-photo visual-first evaluation", "", result["limitations"], "",
        f"Annotation SHA-256: `{result['annotation_sha256']}`.", "",
        (f"Completed {summary['completed_photos']}/{summary['photos']} photos. "
        f"Literal selected {summary['literal_selected']}/{summary['literal_targets']}; "
        f"any same-pass candidate {summary['literal_candidate']}/{summary['literal_targets']}."), "",
        "| Structured field | Targets | Accepted exact | Selected exact | Any candidate exact | Exact held | Wrong accepted | Abstained |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for field, row in summary["by_field"].items():
        lines.append(f"| {field} | {row['targets']} | {row['accepted_exact']} | {row['selected_exact']} | "
            f"{row['candidate_exact']} | {row['exact_held_for_review']} | {row['wrong_accepted']} | {row['abstained']} |")
    lines += ["", ("All counts retain the frozen denominators. Abstention means no accepted structured value, "
        "including review-only output. Matching alternatives do not erase a wrong accepted value. Currency "
        "shape hypotheses remain review-only even when the amount matches. Unit-price currency may be "
        "decoded from its emitted Rs/INR/₹ raw token; country and annotations never supply currency."), "",
        "| Photo / target | Literal | Selected OCR | Any OCR | Structured outcome |",
        "|---|---|---|---|---|"]
    for record in result["images"]:
        for row in record.get("scores", []):
            lines.append(f"| {record['id']} / {row['target']['id']} | {row['target']['literal']} | "
                f"{row['literal_selected']} | {row['literal_candidate']} | {row.get('outcome', 'unscored: ambiguous semantic role')} |")
    lines += ["", ("The two Commons photos retain CC BY-SA attribution in copied source manifests. The two "
        "Nestlé photos retain publisher-added red rectangles and have restricted reuse; they are not an "
        "open redistribution dataset. Source countries do not supply package origin or legal ground truth. "
        "No normalized date, unseen quantity, complete MRP/tax clause, safety or compliance accuracy is scored."), "",
        f"Run complete with unchanged inputs/models/source: **{result['complete']}**.", "",
        ("Per-photo JSON retains complete OCR boxes, candidates, pass diagnostics, extraction provenance, "
        "unscored observations and price hypotheses. evaluated-source contains the exact source snapshot; "
        "environment records model/config/executable/traineddata hashes. Original photographs remain unchanged."), ""]
    return "\n".join(lines)


def run(annotation_path, output, root=ROOT):
    from tula.domain.enums import Panel
    from tula.extract.pipeline import extract
    from tula.ocr.engines import RapidOcrEngine

    annotation_path, output = Path(annotation_path), Path(output)
    data = load_annotations(annotation_path, root)
    if output.exists():
        raise FileExistsError("Use a new output directory; retained runs are immutable")
    output.mkdir(parents=True)
    result = {"dataset_id": data["dataset_id"], "started_at": datetime.now(UTC).isoformat(),
        "annotation_sha256": sha256(annotation_path), "engine": "rapidocr",
        "limitations": data["protocol"] + " " + data["scope"],
        "environment": environment(root), "images": []}
    manifests = [annotation_path, *(annotation_path.parent / name for name in
                 ("FROZEN.json", "sources.json", "additional-sources.json"))]
    result["manifest_sha256"] = {path.name: sha256(path) for path in manifests}
    for path in manifests:
        (output / path.name).write_bytes(path.read_bytes())
    for relative in result["environment"]["source_sha256"]:
        snapshot = output / "evaluated-source" / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((root / relative).read_bytes())
    engine = RapidOcrEngine()
    for record in data["images"]:
        started = time.perf_counter()
        row = {"id": record["id"], "source_path": record["path"], "photo_sha256": record["sha256"]}
        try:
            ocr = engine.read(str(root / record["path"]))
            if ocr.engine != "rapidocr":
                raise RuntimeError("Evaluation requires actual RapidOCR")
            for line in ocr.lines:
                line.frame = record["path"]
            extracted = extract([(Panel(record["panel"]), ocr)])
            raw = {"declarations": {key.value: value.model_dump(mode="json")
                for key, value in extracted.declarations.items()}, "intelligence": extracted.intelligence,
                "gtin_candidates": extracted.gtin_candidates, "warnings": extracted.warnings,
                "full_text": extracted.full_text}
            raw_ocr = asdict(ocr)
            for candidate in raw_ocr["candidates"]:
                candidate["frame"] = record["path"]  # This read call's original image, not inferred metadata.
            row["scores"], row["unscored_observations"] = score_image(record, raw, raw_ocr)
            row["ocr"], row["extraction"] = raw_ocr, raw
            row["price_hypotheses"] = {"quality": raw_ocr["quality"].get("price_context_candidates"),
                "lines": [line for line in raw_ocr["lines"] if line.get("price_hint")],
                "extracted": [item for item in predictions(raw).get("retail_sale_price", [])
                    if item.get("method") == "pixel_currency_context_candidate"]}
        except Exception as exc:  # noqa: BLE001 - retain evidence of failed frames, mark run incomplete.
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        result["images"].append(row)
        (output / f"{record['id']}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{record['id']}: {row.get('error', 'completed')} ({row['elapsed_ms']} ms)", flush=True)
    result["environment_after"] = environment(root)
    result["changed_environment_sections"] = [key for key in result["environment"]
        if result["environment"][key] != result["environment_after"].get(key)]
    result["changed_inputs"] = [path.name for path in manifests
        if sha256(path) != result["manifest_sha256"][path.name]] + [record["path"] for record in data["images"]
        if sha256(root / record["path"]) != record["sha256"]]
    result["summary"] = summarize(data, result["images"])
    result["completed_at"] = datetime.now(UTC).isoformat()
    result["complete"] = not (result["changed_environment_sections"] or result["changed_inputs"]
        or result["summary"]["runtime_errors"]) and result["summary"]["completed_photos"] == len(data["images"])
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(report(result), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, default=ANNOTATIONS)
    parser.add_argument("--out", type=Path, default=ROOT / "out/additional-real-label-evaluation")
    args = parser.parse_args()
    result = run(args.annotations, args.out)
    print(json.dumps(result["summary"], indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
