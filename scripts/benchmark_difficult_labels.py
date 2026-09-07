"""Real CPU OCR on reproducible difficult labels and existing product photos.

Generated images carry known printed strings. Recall below measures exact
normalized value-string recovery, not legal accuracy. Existing photographs
are explicitly unlabelled smoke cases; they do not enter accuracy metrics.

python scripts/benchmark_difficult_labels.py --out out/difficult-ocr
Use --baseline to compare the same model's original-image-only pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import unicodedata
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from tula.labgen import _font
from tula.ocr.engines import RapidOcrEngine

ROWS = ["TULA TEST BISCUITS", "NET WT 200 g", "MRP Rs 180.00", "MFG 08/2026",
        "EXP 09/2027", "BATCH AB1234", "Manufactured by Sample Foods, Delhi 110001"]
VALUES = {"net_quantity": "200g", "mrp": "180.00", "manufacturing": "08/2026",
          "expiry": "09/2027", "batch": "AB1234"}


def normalized(text):
    # Decimal points and date separators are material; 18.00 must never count
    # as recovery of 180.0. Keep combining marks in Indian-script text too.
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if c.isalnum() or unicodedata.category(c).startswith("M") or c in "./-")


def make_label(rows=ROWS, *, dotted=False, split=False, dot_pitch=4):
    image = Image.new("RGB", (1100, 720), (247, 243, 231))
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, 1080, 700), outline=(40, 60, 65), width=3)
    for i, text in enumerate(rows):
        y = 50 + i * 85
        font = _font(False, 38 if i < 6 else 26)
        if any("\u0900" <= c <= "\u097f" for c in text):
            for candidate in ("C:/Windows/Fonts/Nirmala.ttc",
                              "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"):
                if Path(candidate).is_file():
                    font = ImageFont.truetype(candidate, 38)
                    break
            else:
                raise RuntimeError("The Hindi benchmark requires Nirmala.ttc or NotoSansDevanagari-Regular.ttf; missing glyphs are not valid ground truth")
        if split and i in (1, 2):
            cue, value = ("NET WT", "200 g") if i == 1 else ("MRP Rs", "180.00")
            draw.text((60, y), cue, fill="black", font=font)
            draw.text((400, y + 13), value, fill="black", font=font)
        elif dotted and i in (2, 3, 4, 5):
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).text((60, y), text, fill=255, font=font)
            arr = np.asarray(mask)
            for yy in range(y, min(y + 60, image.height), dot_pitch):
                for xx in range(55, 1000, dot_pitch):
                    if arr[yy, xx] > 100:
                        draw.ellipse((xx - 1, yy - 1, xx + 1, yy + 1), fill=(15, 15, 15))
        else:
            draw.text((60, y), text, fill="black", font=font)
    return image


def generated_cases(directory):
    directory.mkdir(parents=True, exist_ok=True)
    clean = make_label()
    cases = []

    def save(name, image, values=None):
        path = directory / f"{name}.png"
        image.save(path)
        cases.append({"name": name, "image": path, "expected": VALUES if values is None else values,
                      "kind": "generated_ground_truth"})

    save("clean", clean)
    save("low_resolution", clean.resize((440, 288)))
    save("tiny_text", clean.resize((330, 216)))
    save("tilted_13_degrees", clean.rotate(13, expand=True, fillcolor="white"))
    save("rotated_90", clean.rotate(90, expand=True))
    save("rotated_180", clean.rotate(180, expand=True))
    save("rotated_270", clean.rotate(270, expand=True))
    save("dot_matrix_mrp_dates", make_label(dotted=True, dot_pitch=3))
    save("severely_sparse_dot_matrix", make_label(dotted=True))
    save("mixed_hindi_english", make_label(["शुद्ध वजन 200 ग्राम" if row.startswith("NET WT") else row for row in ROWS]),
         {**VALUES, "net_quantity": "200ग्राम", "hindi_cue": "शुद्धवजन"})
    save("misaligned_declarations", make_label(split=True))
    stacked = make_label(["TULA BISCUITS", "NET WT", "200 g", "MRP Rs", "180.00", "MFG 08/2026", "EXP 09/2027"])
    save("stacked_keyword_value", stacked, {k: v for k, v in VALUES.items() if k != "batch"})
    save("blurred", clean.filter(ImageFilter.GaussianBlur(2.5)))
    arr = np.array(clean).astype(float)
    save("low_contrast", Image.fromarray(np.uint8(160 + arr * .24)))
    shadow = np.linspace(.24, 1, clean.width)[None, :, None]
    save("shadows", Image.fromarray(np.uint8(arr * shadow)))
    glare = arr.copy()
    y, x = np.mgrid[:clean.height, :clean.width]
    alpha = np.exp(-((x - 450) ** 2 / (100 ** 2) + (y - 250) ** 2 / (190 ** 2)))[:, :, None] * .99
    glare = glare * (1 - alpha) + 255 * alpha
    save("glare", Image.fromarray(glare.astype(np.uint8)))
    rng = np.random.default_rng(26034)
    textured = np.clip(arr + rng.normal(0, 12, arr.shape) + 12 * np.sin(x[:, :, None] / 8), 0, 255)
    save("complex_background", Image.fromarray(textured.astype(np.uint8)))
    h, w = arr.shape[:2]
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    dst = np.float32([[120, 40], [w - 180, 0], [w - 1, h - 20], [0, h - 1]])
    perspective = cv2.warpPerspective(np.uint8(arr), cv2.getPerspectiveTransform(src, dst), (w, h), borderValue=(255, 255, 255))
    save("perspective", Image.fromarray(perspective))
    map_x = x.astype(np.float32)
    map_y = (y + 20 * np.sin((x - w / 2) / w * np.pi)).astype(np.float32)
    curved = cv2.remap(np.uint8(arr), map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    save("curved_baseline", Image.fromarray(curved))
    save("missing_mrp", make_label([row if not row.startswith("MRP") else "" for row in ROWS]),
         {k: v for k, v in VALUES.items() if k != "mrp"})
    save("ambiguous_numeric_date", make_label(["EXP 08/09/26" if row.startswith("EXP") else row for row in ROWS]),
         {**VALUES, "expiry": "08/09/26"})
    front = make_label(ROWS[:3])
    back = make_label(ROWS[3:])
    save("multi_side_front", front, {k: v for k, v in VALUES.items() if k in ("net_quantity", "mrp")})
    save("multi_side_back", back, {k: v for k, v in VALUES.items() if k not in ("net_quantity", "mrp")})
    save("blank", Image.new("RGB", clean.size, "white"), {})
    return cases


def measurements(lines, expected):
    combined = normalized(" ".join(l.text for l in lines))
    reliable = normalized(" ".join(l.text for l in lines if not l.review_required and l.confidence >= .55))
    return {"printed_value_recovery": {k: normalized(v) in combined for k, v in expected.items()},
            "uncontested_value_recovery": {k: normalized(v) in reliable for k, v in expected.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("out/difficult-ocr"))
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--real-limit", type=int, default=10)
    parser.add_argument("--only", help="Comma-separated generated case names for a targeted rerun")
    args = parser.parse_args()
    cases = generated_cases(args.out / "images")
    if args.limit:
        cases = cases[:args.limit]
    if args.only:
        names = set(args.only.split(","))
        cases = [case for case in cases if case["name"] in names]
        if names != {case["name"] for case in cases}:
            parser.error("--only contains an unknown generated case name")
    root = Path(__file__).resolve().parents[1]
    seen = set()
    for path in sorted((root / "data/uploads").glob("*/*.jpg")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen and len(seen) < args.real_limit:
            seen.add(digest)
            cases.append({"name": path.stem, "image": path, "expected": None, "kind": "unlabelled_real_photo"})
    engine = RapidOcrEngine()
    rows = []
    for index, case in enumerate(cases):
        tick = time.perf_counter()
        result = engine.read(str(case["image"]))
        record = {**case, "image": str(case["image"].resolve()), "elapsed_ms": round((time.perf_counter() - tick) * 1000),
                  "sha256": hashlib.sha256(case["image"].read_bytes()).hexdigest(),
                  "selected_lines": [{"text": l.text, "bbox": l.bbox, "confidence": l.confidence,
                                      "review_required": l.review_required, "variants": l.variants} for l in result.lines],
                  "quality": result.quality, "conflicts": result.conflicts, "passes": result.passes,
                  "warnings": result.warnings}
        if case["expected"] is not None:
            record.update(measurements(result.lines, case["expected"]))
        if args.baseline:
            tick = time.perf_counter()
            with engine._lock:
                lines = engine._decode(engine._load()(str(case["image"])))
            record["baseline"] = {"elapsed_ms": round((time.perf_counter() - tick) * 1000),
                                  "text": [l.text for l in lines]}
            if case["expected"] is not None:
                record["baseline"].update(measurements(lines, case["expected"]))
        rows.append(record)
        values = record.get("printed_value_recovery", {})
        print(f"{index + 1}/{len(cases)} {case['name']}: {len(result.lines)} lines, {len(result.conflicts)} conflicts, "
              f"{sum(values.values())}/{len(values)} known values, {record['elapsed_ms']} ms", flush=True)
        summary = {"description": "Exact normalized known printed value recovery; not field extraction accuracy or legal accuracy. Real photos are unlabelled smoke cases.",
                   "generated": sum(r["kind"] == "generated_ground_truth" for r in rows),
                   "real_photos": sum(r["kind"] == "unlabelled_real_photo" for r in rows)}
        for metric in ("printed_value_recovery", "uncontested_value_recovery"):
            flags = [v for r in rows for v in r.get(metric, {}).values()]
            summary[metric] = {"correct": sum(flags), "total": len(flags)}
            baseline_flags = [v for r in rows for v in r.get("baseline", {}).get(metric, {}).values()]
            if baseline_flags:
                summary[f"baseline_{metric}"] = {"correct": sum(baseline_flags), "total": len(baseline_flags)}
        (args.out / "results.json").write_text(json.dumps({"summary": summary, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
