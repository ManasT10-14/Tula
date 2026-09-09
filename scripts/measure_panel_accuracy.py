"""How well does the panel area come out when nobody measured the package?

Run: python scripts/measure_panel_accuracy.py

The claim being tested is the one the Rule 8 workflow now rests on: with a
scale card in frame and no tape measure anywhere, the principal display panel
can be measured from the photograph well enough to select a Table I band.

Rendered labels are used because they are the only labels whose true panel
size is known exactly. That is the method's strength and its limit: a rendered
label sits square in its frame against a clean edge, so these numbers are a
ceiling on what a photograph of a real package on a shelf would give. The
uncertainty the system reports is what matters more than the point estimate,
and coverage -- how often the reported interval actually contains the truth --
is reported first for that reason.
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tula.analyse import AnalyseOptions, Capture, analyse
from tula.labgen import LabelSpec, render

OUT = ROOT / "out" / "panel-accuracy"

# Panels spanning every row of Table I, from a sachet to a carton.
PANELS = [
    (40.0, 60.0), (55.0, 70.0), (70.0, 100.0), (90.0, 130.0),
    (100.0, 150.0), (120.0, 180.0), (150.0, 220.0), (200.0, 300.0),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="panel-accuracy-"))
    rows = []
    for index, (width_mm, height_mm) in enumerate(PANELS):
        spec = LabelSpec(
            panel_w_mm=width_mm, panel_h_mm=height_mm, px_per_mm=12.0,
            # The point of the exercise: no officer typed the panel size in.
            supply_panel_size=False,
            scale_source="aruco_card",
        )
        rendered = render(spec, work, f"panel{index}")
        analysis = analyse([Capture(str(rendered.png))], AnalyseOptions(engine_name="fixture"))
        measured = analysis.package.pdp_area_cm2
        true_area = rendered.truth.pdp_area_cm2
        row = {
            "panel_mm": [width_mm, height_mm],
            "true_area_cm2": round(true_area, 1),
            "measured_area_cm2": round(measured.value, 1) if measured else None,
            "uncertainty_cm2": round(measured.uncertainty, 1) if measured else None,
            "tier": measured.tier.value if measured else None,
        }
        if measured:
            row["relative_error"] = round((measured.value - true_area) / true_area, 4)
            row["interval_covers_truth"] = bool(
                measured.value - measured.uncertainty <= true_area <= measured.value + measured.uncertainty
            )
            row["relative_uncertainty"] = round(measured.uncertainty / measured.value, 4)
        rows.append(row)

    measured_rows = [r for r in rows if r["measured_area_cm2"] is not None]
    errors = [r["relative_error"] for r in measured_rows]
    summary = {
        "panels": len(rows),
        "area_measured_without_a_tape": len(measured_rows),
        "interval_covers_truth": sum(1 for r in measured_rows if r["interval_covers_truth"]),
        "median_relative_error": round(statistics.median(errors), 4) if errors else None,
        "worst_relative_error": round(max(errors, key=abs), 4) if errors else None,
        "median_relative_uncertainty": (
            round(statistics.median(r["relative_uncertainty"] for r in measured_rows), 4)
            if measured_rows else None
        ),
    }
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "protocol": (
            "Rendered labels, whose true panel size is known exactly. A rendered "
            "label fills its frame squarely against a clean edge, so these are a "
            "ceiling on what a photograph of a package on a shelf would give. "
            "Coverage of the reported interval matters more than the point error."
        ),
        "summary": summary,
        "rows": rows,
    }
    (OUT / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"{'panel mm':>14}  {'true cm2':>9}  {'measured':>9}  {'+/-':>7}  {'err':>7}  covers")
    for row in rows:
        if row["measured_area_cm2"] is None:
            print(f"{row['panel_mm'][0]:6.0f}x{row['panel_mm'][1]:<7.0f} {row['true_area_cm2']:9.1f}  "
                  f"{'not measured':>9}")
            continue
        print(f"{row['panel_mm'][0]:6.0f}x{row['panel_mm'][1]:<7.0f} {row['true_area_cm2']:9.1f}  "
              f"{row['measured_area_cm2']:9.1f}  {row['uncertainty_cm2']:7.1f}  "
              f"{row['relative_error']:+7.1%}  {'yes' if row['interval_covers_truth'] else 'NO'}")
    print()
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"\nwritten: {(OUT / 'results.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
