"""Synthetic label generator with ground truth.

This is the test instrument. It draws a real PNG at true millimetre geometry
and reports exactly what it drew, so a measurement coming back out of the
pipeline can be checked against what went in. Without that, "1.33 mm" is a
number nobody can falsify.

It emits three files per label:

  <stem>.png         the image, for the real OCR path
  <stem>.txt         a fixture sidecar with exact geometry, for the perfect-OCR path
  <stem>.meta.json   the capture metadata an Android app would supply

Running the same label through both engines is the useful diagnostic: if a
verdict is wrong under the fixture engine the rules are wrong, and if it is only
wrong under RapidOCR the recognition is wrong. That separation is most of
debugging this system.

It is also the seed of the training-data generator in PRD section 6 -- add
cylindrical warp, specular highlights and motion blur to `render` and the same
ground truth becomes supervision.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\Nirmala.ttc",
    r"C:\Windows\Fonts\Nirmala.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\NirmalaB.ttc",
    r"C:\Windows\Fonts\NirmalaB.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# A TTC holds several faces. On Windows Nirmala UI Bold is face 1 of
# Nirmala.ttc, not a separate NirmalaB file. Keep script selection separate
# from the Latin fallback: Arial/DejaVu cannot draw the Hindi fixture text.
DEVANAGARI_REGULAR_CANDIDATES = [
    (r"C:\Windows\Fonts\Nirmala.ttc", 0),
    (r"C:\Windows\Fonts\Nirmala.ttf", 0),
    (r"C:\Windows\Fonts\mangal.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf", 0),
    ("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf", 0),
    ("/usr/share/fonts/lohit-devanagari/Lohit-Devanagari.ttf", 0),
    ("/Library/Fonts/NotoSansDevanagari-Regular.ttf", 0),
]
DEVANAGARI_BOLD_CANDIDATES = [
    (r"C:\Windows\Fonts\Nirmala.ttc", 1),
    (r"C:\Windows\Fonts\NirmalaB.ttf", 0),
    (r"C:\Windows\Fonts\mangalb.ttf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf", 0),
    ("/Library/Fonts/NotoSansDevanagari-Bold.ttf", 0),
]

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


@lru_cache(maxsize=32)
def _font_codepoints(path: str, index: int) -> frozenset[int]:
    """Read the actual cmap; a visible .notdef box is not a supported glyph."""
    from reportlab.pdfbase.ttfonts import TTFont

    face = TTFont("TulaFixtureProbe", path, subfontIndex=index).face
    return frozenset(code for code, glyph in face.charToGlyph.items() if glyph != 0)


@lru_cache(maxsize=128)
def _script_font(path: str, index: int, size_px: int):
    return ImageFont.truetype(path, size_px, index=index, layout_engine=ImageFont.Layout.RAQM)


def _devanagari_font(bold: bool, size_px: int, text: str):
    from reportlab.pdfbase.ttfonts import TTFError

    if not features.check_feature("raqm"):
        raise ValueError("Hindi fixture rendering requires Pillow with RAQM text shaping. "
                         "Install a Pillow wheel with RAQM support, or set hindi_net_qty=False "
                         "and use only Latin text for this scenario.")
    required = {ord(c) for c in text if not c.isspace() and c not in "\u200c\u200d"}
    candidates = [*(DEVANAGARI_BOLD_CANDIDATES if bold else []), *DEVANAGARI_REGULAR_CANDIDATES]
    for path, index in candidates:
        if not Path(path).is_file():
            continue
        try:
            if required <= _font_codepoints(path, index):
                return _script_font(path, index, size_px)
        except (OSError, TTFError):
            continue
    raise ValueError("No installed font covers the Hindi fixture text. Install Windows Nirmala UI "
                     "or Noto Sans Devanagari (fonts-noto-core on Debian/Ubuntu); otherwise set "
                     "hindi_net_qty=False and use only Latin text. No Hindi fixture was generated.")


def _font(bold: bool, size_px: int, text: str = ""):
    if any("\u0900" <= c <= "\u097f" or "\ua8e0" <= c <= "\ua8ff" for c in text):
        return _devanagari_font(bold, size_px, text)
    key = ("b" if bold else "r", size_px)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    for path in (BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size_px)
                _FONT_CACHE[key] = font
                return font
            except OSError:
                pass  # Try the next installed font.
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _cap_height_px(font) -> float:
    """Rendered cap height of digits -- the quantity Rule 8 actually governs."""
    box = font.getbbox("0123456789")
    return float(box[3] - box[1])


def _font_for_cap_height(bold: bool, target_px: float, text: str = ""):
    """Binary-search the point size whose rendered cap height hits the target.

    Nominal font size is not cap height, and the gap varies by face. Searching
    on the rendered raster is what makes the ground truth trustworthy.
    """
    lo, hi, best = 4, 500, 4
    while lo <= hi:
        mid = (lo + hi) // 2
        if _cap_height_px(_font(bold, mid, text)) <= target_px:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return _font(bold, best, text)


@dataclass
class LabelSpec:
    """Everything the bench can vary about a label."""

    # identity
    brand: str = "Crunchy Gold"
    generic: str = "Biscuits"
    package_category: str = "food"
    assessment_date: date = field(default_factory=date.today)
    is_imported: bool = False

    # net quantity -- value and unit split so "gms" can be tested separately
    net_qty_value: str = "200"
    net_qty_unit: str = "g"
    net_qty_mm: float = 3.0  # cap height to draw, in millimetres
    hindi_net_qty: bool = True

    # price. `None` omits the declaration entirely, which is how a genuinely
    # non-compliant pack is drawn -- as opposed to one whose MRP panel simply
    # was not photographed. The bench needs both to be expressible.
    mrp: float | None = 45.0
    tax_clause: bool = True
    second_mrp: float | None = None  # dual pricing
    unit_price: float | None = 0.23
    unit_price_per: str = "1 g"

    # the rest of the mandatory block
    manufacturer: str = "Gold Foods Pvt Ltd"
    address: str = "Plot 42, MIDC Industrial Estate, Pune 411018"
    consumer_care: str | None = "Gold Foods Pvt Ltd, Plot 42, Pune 411018; care@goldfoods.co.in; 1800 200 1234"
    packing_date: date = field(default_factory=lambda: date(2026, 3, 1))
    # A real Indian food label carries a best-before or use-by date beside its
    # manufacturing date, and the generated labels are meant to be compliant
    # except for the one defect a scenario is testing. Left as `None` this is
    # derived from the packing date, so a scenario that moves that date does
    # not silently produce a best-before earlier than it. Set `no_best_before`
    # to draw a food label that genuinely lacks one.
    best_before: date | None = None
    no_best_before: bool = False
    origin: str | None = "Made in India"
    gtin: str | None = "8901234567890"

    # geometry and capture
    panel_w_mm: float = 120.0
    panel_h_mm: float = 180.0
    px_per_mm: float = 12.0
    body_text_mm: float = 3.8
    scale_source: str = "device_depth"  # device_depth|aruco_card|mono_metric
    supply_panel_size: bool = True  # whether the officer measured the panel
    # A rendered label carries every declaration on one face. Set False to
    # simulate a photograph of one side of a package whose other faces were
    # never captured -- the degraded-evidence scenarios rely on this.
    covers_all_declarations: bool = True
    # Draw nothing at all: stands in for a frame the recogniser cannot read,
    # which is the case that used to produce six violations from no evidence.
    blank_panel: bool = False

    def __post_init__(self):
        from .services.context import CATEGORIES
        if self.package_category not in CATEGORIES or self.package_category == "unknown":
            raise ValueError("Choose a known category for the generated scenario.")
        if not isinstance(self.assessment_date, date) or not isinstance(self.is_imported, bool):
            raise TypeError("Scenario assessment date and imported status are invalid.")
        for name in ("brand", "generic", "net_qty_value", "net_qty_unit", "unit_price_per", "manufacturer", "address", "consumer_care", "origin", "gtin"):
            value = getattr(self, name)
            if value is not None and len(value) > 512:
                raise ValueError(f"{name} must be at most 512 characters")
        for name in ("panel_w_mm", "panel_h_mm", "px_per_mm", "net_qty_mm", "body_text_mm"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.panel_w_mm * self.panel_h_mm * self.px_per_mm ** 2 > 25_000_000:
            raise ValueError("Rendered label must be at most 25 megapixels")
        if self.scale_source not in ("device_depth", "aruco_card", "mono_metric", "geometry_prior"):
            raise ValueError("Raster labels require an estimated scale source")
        for name in ("mrp", "second_mrp", "unit_price"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.best_before is None and not self.no_best_before:
            # Nine months is an unremarkable shelf life for a packaged snack.
            self.best_before = self.packing_date + timedelta(days=270)
        if self.best_before is not None and self.best_before < self.packing_date:
            raise ValueError("A best-before date cannot precede the packing date")

    def net_qty_text(self) -> str:
        return f"{self.net_qty_value} {self.net_qty_unit}"


@dataclass
class LineRecord:
    text: str
    bbox: tuple[int, int, int, int]
    cap_height_px: float

    @property
    def cap_height_mm_at(self):
        return lambda px_per_mm: self.cap_height_px / px_per_mm


@dataclass
class GroundTruth:
    """What was actually drawn. The bench compares measurements against this."""

    net_qty_cap_height_mm: float
    smallest_declaration_mm: float
    pdp_area_cm2: float
    net_qty_base: float | None
    unit_price_expected: float | None


@dataclass
class RenderResult:
    png: Path
    fixture: Path
    meta: Path
    truth: GroundTruth
    lines: list[LineRecord]


_UNIT_FACTOR = {"g": 1.0, "kg": 1000.0, "mg": 0.001, "gms": 1.0, "gm": 1.0,
                "Kg": 1000.0, "ml": 1.0, "ML": 1.0, "l": 1000.0, "L": 1000.0,
                "Ltr": 1000.0, "mL": 1.0}


def render(spec: LabelSpec, out_dir: str | Path, stem: str = "label") -> RenderResult:
    if not stem or Path(stem).name != stem or any(c in stem for c in ("/", "\\", ":")):
        raise ValueError("Label stem must be a simple filename")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    width = int(spec.panel_w_mm * spec.px_per_mm)
    height = int(spec.panel_h_mm * spec.px_per_mm)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    margin = int(6 * spec.px_per_mm)
    y = float(margin)
    lines: list[LineRecord] = []

    def write(text: str, height_mm: float, bold: bool = False, gap_mm: float = 2.2):
        nonlocal y
        if spec.blank_panel:
            return None  # unreadable frame: nothing drawn, nothing recorded
        font = _font_for_cap_height(bold, height_mm * spec.px_per_mm, text)
        available = width - 2 * margin
        if font.getlength(text) > available and " " in text:
            words = text.split()
            rows, row = [], ""
            for word in words:
                candidate = f"{row} {word}".strip()
                if row and font.getlength(candidate) > available:
                    rows.append(row)
                    row = word
                else:
                    row = candidate
            rows.append(row)
            if len(rows) > 1:
                last = None
                for row in rows:
                    last = write(row, height_mm, bold, min(gap_mm, 1.0))
                return last
        draw.text((margin, y), text, fill="black", font=font)
        box = draw.textbbox((margin, y), text, font=font)
        lines.append(
            LineRecord(
                text=text,
                bbox=(int(box[0]), int(box[1]), int(box[2]), int(box[3])),
                cap_height_px=_cap_height_px(font),
            )
        )
        y = box[3] + gap_mm * spec.px_per_mm
        return lines[-1]

    body = spec.body_text_mm

    write(spec.brand, 9.0, bold=True, gap_mm=1.6)
    write(spec.generic, 4.5, gap_mm=5.0)

    net_qty_lines: list[LineRecord] = []
    if spec.hindi_net_qty:
        hindi = write(f"शुद्ध वजन {spec.net_qty_text()}", spec.net_qty_mm, bold=True, gap_mm=1.4)
        if hindi is not None:
            net_qty_lines.append(hindi)
    english = write(f"Net Wt. {spec.net_qty_text()}", spec.net_qty_mm, bold=True, gap_mm=4.0)
    if english is not None:
        net_qty_lines.append(english)

    if spec.mrp is not None:
        mrp_text = f"MRP Rs. {spec.mrp:.2f}"
        if spec.tax_clause:
            mrp_text += " inclusive of all taxes"
        write(mrp_text, body + 0.4)
    if spec.second_mrp is not None:
        write(f"Revised MRP Rs. {spec.second_mrp:.2f}", body + 0.4)
    if spec.unit_price is not None:
        write(f"Unit Sale Price: Rs. {spec.unit_price:.2f} per {spec.unit_price_per}", body)

    write(f"Manufactured by: {spec.manufacturer}", body, gap_mm=1.1)
    write(spec.address, body - 0.2, gap_mm=1.1)
    write(f"Mfg: {spec.packing_date.month:02d}/{spec.packing_date.year}", body, gap_mm=1.1)
    if spec.best_before is not None and not spec.no_best_before:
        write(f"Best Before: {spec.best_before.strftime('%d-%b-%Y').upper()}", body)
    if spec.consumer_care:
        write(f"Consumer Care: {spec.consumer_care}", body - 0.2, gap_mm=1.1)
    if spec.origin:
        write(spec.origin, body, gap_mm=1.6)
    if spec.gtin:
        write(spec.gtin, body + 0.8)

    clipped = any(l.bbox[2] > width or l.bbox[3] > height for l in lines)
    # Fixture text must describe pixels actually present, never clipped lines.
    lines = [l for l in lines if l.bbox[2] <= width and l.bbox[3] <= height]
    net_qty_lines = [l for l in net_qty_lines if l in lines]
    png = out_dir / f"{stem}.png"
    image.save(png)

    # ---- fixture sidecar: exact geometry, i.e. a perfect recogniser --------
    fixture = out_dir / f"{stem}.txt"
    fixture.write_text(
        "# generated by tula.labgen -- exact geometry, stands in for perfect OCR\n"
        + "\n".join(
            f"{l.bbox[0]},{l.bbox[1]},{l.bbox[2]},{l.bbox[3]},{l.cap_height_px:.2f}|{l.text}"
            for l in lines
        )
        + "\n",
        encoding="utf-8",
    )

    # ---- capture metadata --------------------------------------------------
    meta_payload = {
        "panel": "pdp",
        "mm_per_px": round(1.0 / spec.px_per_mm, 8),
        "mm_per_px_source": spec.scale_source,
        "packing_date": spec.packing_date.isoformat(),
        # A rendered label is one flat face carrying every declaration there is,
        # so absence on it really is absence -- unlike a photograph of one side
        # of a box. `covers_all_declarations` is how the bench earns the right to
        # assert a missing declaration is missing.
        "covers_all_declarations": spec.covers_all_declarations and not clipped,
        "notes": f"synthetic render at {spec.px_per_mm:g} px/mm",
    }
    if spec.supply_panel_size:
        meta_payload["pdp_width_mm"] = spec.panel_w_mm
        meta_payload["pdp_height_mm"] = spec.panel_h_mm
    meta = out_dir / f"{stem}.meta.json"
    meta.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    # ---- ground truth ------------------------------------------------------
    factor = _UNIT_FACTOR.get(spec.net_qty_unit)
    try:
        qty_base = float(spec.net_qty_value) * factor if factor else None
    except ValueError:
        qty_base = None

    per_base = None
    parts = spec.unit_price_per.split()
    if len(parts) == 2 and parts[1] in _UNIT_FACTOR:
        per_base = float(parts[0]) * _UNIT_FACTOR[parts[1]]
    elif len(parts) == 1 and parts[0] in _UNIT_FACTOR:
        per_base = _UNIT_FACTOR[parts[0]]

    truth = GroundTruth(
        net_qty_cap_height_mm=(
            min(l.cap_height_px for l in net_qty_lines) / spec.px_per_mm
            if net_qty_lines else 0.0
        ),
        smallest_declaration_mm=(
            min(l.cap_height_px for l in lines[2:]) / spec.px_per_mm
            if len(lines) > 2 else 0.0
        ),
        pdp_area_cm2=spec.panel_w_mm * spec.panel_h_mm / 100.0,
        net_qty_base=qty_base,
        unit_price_expected=(
            spec.mrp / qty_base * per_base
            if spec.mrp is not None and qty_base and per_base
            else None
        ),
    )
    return RenderResult(png=png, fixture=fixture, meta=meta, truth=truth, lines=lines)
