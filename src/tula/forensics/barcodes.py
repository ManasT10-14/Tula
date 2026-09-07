"""Decode retail barcode symbols independently of OCR transcription."""
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
