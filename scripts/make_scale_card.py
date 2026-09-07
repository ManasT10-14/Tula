"""Generate the printable LM Scale Card.

An inspector prints this once, cuts it out, and holds it beside the package.
It is the cheapest route to Tier B evidence: no depth sensor, no particular
phone, just a known physical length in the same plane as the label.

The markers are printed at an exact millimetre size, so `metrology.from_aruco`
can turn a detected marker side into mm-per-pixel. Print at 100 percent scale
with "fit to page" switched off, then verify with the ruler along the bottom --
if the printed ruler does not measure true, the card is void.

    python scripts/make_scale_card.py --out out/lm-scale-card.pdf
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import cv2
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MARKER_MM = 25.0
MARKER_IDS = (0, 1, 2, 3)


def _marker_image(marker_id: int, pixels: int = 600):
    try:
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        image = cv2.aruco.generateImageMarker(dictionary, marker_id, pixels)
    except AttributeError:  # older OpenCV
        dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
        image = cv2.aruco.drawMarker(dictionary, marker_id, pixels)
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("could not encode marker")
    return ImageReader(io.BytesIO(buffer.tobytes()))


def build(path: Path, marker_mm: float = MARKER_MM) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    _width, height = A4

    left, top = 20 * mm, height - 24 * mm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, top, "LM Scale Card")
    c.setFont("Helvetica", 9)
    c.drawString(left, top - 14, "Legal Metrology field reference · Tula")

    c.setFont("Helvetica", 8.5)
    lines = [
        f"Each black marker is exactly {marker_mm:g} mm x {marker_mm:g} mm.",
        "Print at 100% scale. Turn OFF 'fit to page' and 'shrink oversized pages'.",
        "Verify with the ruler below before first use: 100 mm must measure 100 mm.",
        "Place the card flat, beside the package and in the same plane as the label.",
        "One marker in frame is enough; more markers give a better estimate.",
    ]
    y = top - 34
    for line in lines:
        c.drawString(left, y, line)
        y -= 11

    # ---- markers -------------------------------------------------------
    y_markers = y - 12 - marker_mm * mm
    for index, marker_id in enumerate(MARKER_IDS):
        x = left + index * (marker_mm + 12) * mm
        c.drawImage(_marker_image(marker_id), x, y_markers,
                    width=marker_mm * mm, height=marker_mm * mm)
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(x + marker_mm * mm / 2, y_markers - 8, f"id {marker_id}")

    # ---- verification ruler --------------------------------------------
    ruler_y = y_markers - 34
    c.setFont("Helvetica-Bold", 8)
    c.drawString(left, ruler_y + 20, "Verification ruler — 0 to 100 mm")

    c.setLineWidth(0.6)
    c.line(left, ruler_y, left + 100 * mm, ruler_y)
    for millimetre in range(101):
        x = left + millimetre * mm
        if millimetre % 10 == 0:
            length, label = 9, True
        elif millimetre % 5 == 0:
            length, label = 6, False
        else:
            length, label = 3, False
        c.line(x, ruler_y, x, ruler_y + length)
        if label:
            c.setFont("Helvetica", 6)
            c.drawCentredString(x, ruler_y + 12, str(millimetre))

    # ---- reference squares ---------------------------------------------
    box_y = ruler_y - 46
    c.setFont("Helvetica-Bold", 8)
    c.drawString(left, box_y + 30, "Reference squares")
    c.setLineWidth(0.5)
    for size, x_offset in ((10, 0), (20, 18), (5, 46)):
        c.rect(left + x_offset * mm, box_y, size * mm, size * mm)
        c.setFont("Helvetica", 6)
        c.drawString(left + x_offset * mm, box_y - 8, f"{size} mm")

    # ---- footer ---------------------------------------------------------
    c.setFont("Helvetica-Oblique", 7.5)
    c.drawString(
        left, 18 * mm,
        "Measurements made with this card are Tier B evidence and may sustain a finding "
        "under Rule 8. Without it, measurements are Tier C and advisory only.",
    )
    c.showPage()
    c.save()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the printable LM Scale Card.")
    parser.add_argument("--out", default="out/lm-scale-card.pdf")
    parser.add_argument("--marker-mm", type=float, default=MARKER_MM)
    args = parser.parse_args()
    path = build(Path(args.out), args.marker_mm)
    print(f"wrote {path}")
    print(f"markers are {args.marker_mm:g} mm; pass --marker-mm to match if you rescale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
