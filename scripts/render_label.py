"""Render a synthetic package label as a real PNG.

Two jobs. First, it lets the actual OCR path be exercised end to end without
photographing anything. Second, it is the seed of the synthetic data generator
in PRD section 6: the same composer, plus physical degradations (cylindrical
warp, specular highlights, motion blur, low light), produces the labelled
training data that does not otherwise exist for this task -- and crucially,
labelled *violations*, which are rare in the wild.

The millimetre geometry is honest: the canvas is sized from a real panel size
and a stated pixel density, so a numeral asked for at 1.4 mm really is 1.4 mm
on the rendered artwork. That makes the output usable as metrology ground truth.

    python scripts/render_label.py --out data/samples/rendered-front.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Windows faces that cover Devanagari as well as Latin.
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\seguiemj.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(paths, size_px: int):
    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size_px)
            except OSError:
                pass  # Try the next installed font.
    return ImageFont.load_default()


def _cap_height_px(font, sample: str = "0123456789") -> float:
    """Actual rendered cap height of digits -- the thing Rule 8 measures."""
    box = font.getbbox(sample)
    return float(box[3] - box[1])


def render(
    out: Path,
    *,
    panel_w_mm: float = 120.0,
    panel_h_mm: float = 180.0,
    px_per_mm: float = 12.0,
    brand: str = "Crunchy Gold",
    generic: str = "Biscuits",
    net_qty: str = "200 g",
    net_qty_mm: float = 1.4,
    mrp: str = "MRP Rs. 45.00",
    tax_clause: bool = False,
    unit_price: str | None = "Unit Sale Price: Rs. 20.00 per 100 g",
    hindi_net_qty: str | None = None,
    gtin: str | None = "6901234567892",
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    width = int(panel_w_mm * px_per_mm)
    height = int(panel_h_mm * px_per_mm)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    margin = int(6 * px_per_mm)
    y = margin

    def write(text: str, height_mm: float, bold: bool = False, gap_mm: float = 2.4):
        nonlocal y
        # binary-search a point size whose *rendered cap height* matches the
        # requested millimetres, rather than trusting the nominal font size
        target = height_mm * px_per_mm
        lo, hi, best = 4, 400, 4
        while lo <= hi:
            mid = (lo + hi) // 2
            font = _font(BOLD_CANDIDATES if bold else FONT_CANDIDATES, mid)
            if _cap_height_px(font) <= target:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        font = _font(BOLD_CANDIDATES if bold else FONT_CANDIDATES, best)
        draw.text((margin, y), text, fill="black", font=font)
        box = draw.textbbox((margin, y), text, font=font)
        y = box[3] + int(gap_mm * px_per_mm)

    write(brand, 9.0, bold=True, gap_mm=1.6)
    write(generic, 4.5, gap_mm=6.0)

    if hindi_net_qty:
        write(hindi_net_qty, net_qty_mm, bold=True, gap_mm=1.4)
    write(f"Net Wt. {net_qty}", net_qty_mm, bold=True, gap_mm=4.0)

    write(mrp + (" inclusive of all taxes" if tax_clause else ""), 2.6)
    if unit_price:
        write(unit_price, 2.2)
    write("Manufactured by: Gold Foods Pvt Ltd", 2.2, gap_mm=1.2)
    write("Plot 42, MIDC Industrial Estate, Pune 411018", 2.0, gap_mm=1.2)
    write("Mfg: 03/2026", 2.2, gap_mm=1.2)
    write("Consumer Care: care@goldfoods.co.in, 1800 200 1234", 2.0, gap_mm=1.2)
    write("Made in India", 2.2, gap_mm=2.0)
    if gtin:
        write(gtin, 3.0)

    image.save(out)

    # The sidecar states the true scale, so the metrology engine has a Tier B
    # source. On a real capture the Android app writes this from ARCore depth.
    out.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "panel": "pdp",
                "mm_per_px": round(1.0 / px_per_mm, 6),
                "mm_per_px_source": "device_depth",
                "pdp_width_mm": panel_w_mm,
                "pdp_height_mm": panel_h_mm,
                "packing_date": "2026-03-01",
                "notes": f"synthetic render at {px_per_mm:g} px/mm; "
                         f"net quantity drawn at {net_qty_mm:g} mm cap height",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a synthetic label image.")
    parser.add_argument("--out", default="data/samples/rendered-front.png")
    parser.add_argument("--net-qty", default="200 g")
    parser.add_argument("--net-qty-mm", type=float, default=1.4,
                        help="cap height in mm; below 2.0 violates Rule 8(2) "
                             "for a 120x180 mm panel")
    parser.add_argument("--tax-clause", action="store_true")
    parser.add_argument("--hindi", action="store_true")
    args = parser.parse_args()

    path = render(
        Path(args.out),
        net_qty=args.net_qty,
        net_qty_mm=args.net_qty_mm,
        tax_clause=args.tax_clause,
        hindi_net_qty="शुद्ध वजन 200 ग्राम" if args.hindi else None,
    )
    print(f"wrote {path} and {path.with_suffix('.meta.json').name}")
    print(f"net quantity drawn at {args.net_qty_mm:g} mm cap height")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
