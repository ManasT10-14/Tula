"""Reproducible CPU OCR audit: generated cases plus existing real photographs.

Run after installing the project: python scripts/audit_images.py
Real photographs are smoke tests, not legally labelled accuracy ground truth.
"""
import json
from pathlib import Path

from PIL import Image, ImageFilter

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.bench import SCENARIOS, run_scenario
from tula.domain.models import sha256_file
from tula.labgen import LabelSpec, render


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "out" / "audit"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for engine in ("fixture", "rapidocr"):
        for scenario in SCENARIOS:
            result = run_scenario(scenario, engine_name=engine, work_dir=out / engine)
            a = result.analysis
            row = {"name": scenario.key, "engine": engine, "ms": result.elapsed_ms,
                   "image": str(result.image), "lines": len(a.spans), "checks_ok": result.ok,
                   "mismatches": [vars(c) for c in result.failed],
                   "truth": [{"quantity": c.quantity, "drawn": c.drawn,
                              "measured": c.measured.model_dump(mode="json") if c.measured else None,
                              "within_interval": c.within_interval} for c in result.truth_checks],
                   "counts": a.counts()}
            rows.append(row)
            (out / f"{engine}-{scenario.key}.json").write_text(a.model_dump_json(indent=2), encoding="utf-8")
            print(f"{engine} {scenario.key}: {'OK' if result.ok else 'REVIEW'} ({result.elapsed_ms} ms)", flush=True)

    generated = render(LabelSpec(), out / "stress", "source")
    with Image.open(generated.png) as im:
        im.rotate(90, expand=True).save(out / "stress" / "rotated.png")
        im.filter(ImageFilter.GaussianBlur(12)).save(out / "stress" / "blurred.png")
    Image.new("RGB", (1200, 800), "white").save(out / "stress" / "blank.png")
    seen = set()
    real = []
    for image in sorted((root / "data" / "uploads").glob("*/*.jpg")):
        digest = sha256_file(str(image))
        if digest not in seen:
            real.append(image)
            seen.add(digest)
    images = real + [out / "stress" / f"{s}.png" for s in ("rotated", "blurred", "blank")]
    for image in images:
        a = analyse([Capture(str(image))], AnalyseOptions(engine_name="rapidocr"))
        name = image.stem
        (out / f"photo-{name}.json").write_text(a.model_dump_json(indent=2), encoding="utf-8")
        rows.append({"name": name, "engine": "rapidocr", "kind": "photo_smoke", "image": str(image),
                     "ms": a.elapsed_ms, "lines": len(a.spans), "counts": a.counts(),
                     "violations": [{"rule": f.rule_id, "text": f.extracted, "detail": f.detail} for f in a.violations]})
        print(f"photo {name}: {len(a.spans)} lines, {len(a.violations)} adverse findings ({a.elapsed_ms} ms)", flush=True)
    (out / "image-results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(rows)} runs to {out / 'image-results.json'}", flush=True)
    return 1 if any(not r.get("checks_ok", True) for r in rows if r["engine"] == "fixture") else 0


if __name__ == "__main__":
    raise SystemExit(main())
