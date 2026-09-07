"""Resolve only recorded, integrity-checked evidence for UI and exports."""
import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from ..domain.models import Analysis, sha256_file


def observation_sources(item: dict) -> list[dict]:
    """Locate a printed additive code within its recorded ingredient block."""
    sources = item.get("sources", [])
    code = item.get("code")
    if not code:
        return sources
    normalized = re.sub(r"[\s:-]+", "", str(code)).casefold()
    pattern = r"\b(?:INS\s*[-:]?\s*\d{3,4}[a-z]?|E\s*-?\s*\d{3,4}[a-z]?)\b"
    located = [source for source in sources if any(
        re.sub(r"[\s:-]+", "", match[0]).casefold() == normalized
        for match in re.finditer(pattern, str(source.get("text", "")), re.IGNORECASE)
    )]
    return located or sources


def verify(analysis: Analysis) -> list[dict]:
    records = []
    for frame in analysis.scan.frames:
        expected = analysis.scan.frame_hashes.get(frame) or analysis.scan.frame_hashes.get(Path(frame).name)
        actual = sha256_file(frame) if Path(frame).is_file() else None
        records.append({"frame": frame, "expected": expected, "actual": actual,
                        "status": "verified" if expected and actual == expected else "missing" if actual is None else "mismatch"})
    for frame, expected in analysis.scan.original_frame_hashes.items():
        actual = sha256_file(frame) if Path(frame).is_file() else None
        records.append({"frame": frame, "expected": expected, "actual": actual,
                        "status": "verified" if expected and actual == expected else "missing" if actual is None else "mismatch"})
    return records


def crops(analysis: Analysis):
    from ..extract.provenance import get_provenance
    verified = {r["frame"] for r in verify(analysis) if r["status"] == "verified"}
    regions = []
    for klass, decl in analysis.declarations.items():
        provenance = get_provenance(decl)
        for source in [*provenance.sources, *provenance.original_sources]:
            if source.frame in verified and source.bbox:
                regions.append((klass.value, source.frame, source.bbox))
        if not decl.frame or decl.frame not in verified or not decl.bbox:
            continue
        x0, y0, x1, y1 = decl.bbox
        # Contact declarations span multiple OCR lines. Show the entire
        # recorded block without changing the line box used by metrology.
        texts = set(decl.raw.splitlines())
        for span in analysis.spans:
            if span.frame == decl.frame and span.text in texts and span.bbox:
                sx0, sy0, sx1, sy1 = span.bbox
                x0, y0, x1, y1 = min(x0,sx0), min(y0,sy0), max(x1,sx1), max(y1,sy1)
        for source in decl.norm.get("source_spans", []):
            if source.get("frame") == decl.frame and source.get("bbox"):
                sx0, sy0, sx1, sy1 = source["bbox"]
                x0, y0, x1, y1 = min(x0,sx0), min(y0,sy0), max(x1,sx1), max(y1,sy1)
        regions.append((klass.value, decl.frame, (x0, y0, x1, y1)))
    for name, items in analysis.intelligence.get("fields", {}).items():
        for item in items:
            for source in item.get("sources", []):
                if source.get("frame") in verified and source.get("bbox"):
                    regions.append((name, source["frame"], tuple(source["bbox"])))
    product = analysis.intelligence.get("product", {})
    additional = [("dietary claim", analysis.intelligence.get("dietary", [])),
                  ("additive identifier", analysis.intelligence.get("additives", [])),
                  ("category evidence", product.get("category_evidence", [])),
                  ("origin evidence", product.get("origin_evidence", [])),
                  ("package reference", product.get("package_type_evidence", []))]
    for name, items in additional:
        for item in items:
            for source in observation_sources(item):
                if source.get("frame") in verified and source.get("bbox"):
                    regions.append((name, source["frame"], tuple(source["bbox"])))
    seen = set()
    for name, frame, region in regions:
        if (frame, region) in seen:
            continue
        seen.add((frame, region))
        with Image.open(frame) as im:
            x0, y0, x1, y1 = map(int, region)
            box = (max(0,x0-8), max(0,y0-8), min(im.width,x1+8), min(im.height,y1+8))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            crop = im.crop(box).convert("RGB")
            draw = ImageDraw.Draw(crop)
            draw.rectangle((max(0, x0-box[0]), max(0, y0-box[1]),
                            min(crop.width-1, x1-box[0]), min(crop.height-1, y1-box[1])),
                           outline=(181, 54, 19), width=2)
            stream = BytesIO()
            crop.save(stream, format="PNG")
            stream.seek(0)
            yield name, frame, box, stream, crop.size


def originals(analysis: Analysis):
    """Full original uploads, or recorded captures when no upload source exists.

    Rendering applies EXIF orientation for viewing only. The hash refers to the
    preserved file, not to this report illustration.
    """
    verified = {r["frame"] for r in verify(analysis) if r["status"] == "verified"}
    frames = list(analysis.scan.original_frame_hashes) or analysis.scan.frames
    for index, frame in enumerate(frames, 1):
        if frame not in verified:
            continue
        with Image.open(frame) as im:
            image = ImageOps.exif_transpose(im).convert("RGB")
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            stream = BytesIO()
            image.save(stream, format="PNG")
            stream.seek(0)
            label = "Original upload" if analysis.scan.original_frame_hashes else "Recorded capture"
            yield f"{label} {index}", frame, stream, image.size
