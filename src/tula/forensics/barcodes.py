"""Decode retail barcode symbols independently of OCR transcription."""
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


def decode(image_path: str) -> list[str]:
    if not Path(image_path).is_file():
        return []
    import zxingcpp

    with Image.open(image_path) as image:
        formats = [zxingcpp.EAN13, zxingcpp.EAN8, zxingcpp.UPCA, zxingcpp.ITF]
        results = zxingcpp.read_barcodes(image, formats=formats)
    # ITF is also used for arbitrary identifiers: only GTIN-14 is eligible.
    return [r.text for r in results if r.text.isdigit()
            and len(r.text) in (8, 12, 13, 14)
            and (r.format != zxingcpp.ITF or len(r.text) == 14)]


@dataclass(frozen=True)
class Located:
    """One decoded symbol and the width it occupies in the source image."""

    text: str
    symbology: str
    pixel_width: float


def locate(image_path: str) -> list[Located]:
    """Decode symbols and measure how wide each is printed, in pixels.

    The width is what makes a barcode usable as a ruler: its module count is
    fixed by the symbology and its module width is bounded by the GS1
    magnification range, so the pixel width brackets the scale of the label.

    The measured span is the top edge, and it is rejected when the symbol is
    noticeably rotated in frame. A width read across a tilted symbol is
    foreshortened, and a foreshortened ruler reports a package smaller than it
    is -- which is the direction that invents shortfalls.
    """
    if not Path(image_path).is_file():
        return []
    import zxingcpp

    with Image.open(image_path) as image:
        formats = [zxingcpp.EAN13, zxingcpp.EAN8, zxingcpp.UPCA]
        results = zxingcpp.read_barcodes(image, formats=formats)

    output: list[Located] = []
    for result in results:
        position = getattr(result, "position", None)
        if position is None:
            continue
        top_left, top_right = position.top_left, position.top_right
        bottom_left = position.bottom_left
        width = float(abs(top_right.x - top_left.x))
        rise = float(abs(top_right.y - top_left.y))
        height = float(abs(bottom_left.y - top_left.y))
        if width <= 0 or height <= 0:
            continue
        # More than roughly 5 degrees of rotation and the horizontal span
        # understates the symbol's true printed width.
        if rise > 0.09 * width:
            continue
        output.append(Located(text=result.text, symbology=str(result.format),
                              pixel_width=width))
    return output
