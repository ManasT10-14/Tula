"""Validate originals, apply explicit non-destructive capture edits and record provenance."""
from __future__ import annotations

import json
import math
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from ..analyse import Capture
from ..domain.enums import Panel
from ..domain.models import sha256_file


class CaptureError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def receive(files, destination, panels="pdp", edits="[]", dimensions="{}"):
    if not files or not files[0].filename:
        raise CaptureError("Choose at least one image of the package.", 400)
    if len(files) > 12:
        raise CaptureError("Upload at most 12 images for one package.", 413)
    try:
        panel_list = [Panel(p.strip()) for p in panels.split(",") if p.strip()]
        transforms = json.loads(edits)
        geometry = json.loads(dimensions)
        if not isinstance(transforms, list) or not isinstance(geometry, dict):
            raise TypeError()
        if len(transforms) > len(files):
            raise CaptureError("Image edits must correspond to uploaded images.")
        if len(panel_list) not in (0, 1, len(files)):
            raise CaptureError("Assign a panel to each image in upload order.")
    except (ValueError, TypeError):
        raise CaptureError("Choose valid panels and image edits.") from None
    root = Path(destination).resolve()
    session = root / uuid.uuid4().hex
    session.mkdir(parents=True)
    captures, hashes, edit_records = [], {}, []
    try:
        for index, upload in enumerate(files):
            original = session / f"{index:02d}-original.bin"
            total = 0
            with original.open("wb") as fh:
                while chunk := upload.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 25 * 1024 * 1024:
                        raise CaptureError("Each image must be 25 MB or smaller.", 413)
                    fh.write(chunk)
            target = session / f"{index:02d}-frame.png"
            transform = transforms[index] if index < len(transforms) else {}
            if not isinstance(transform, dict):
                raise CaptureError("Invalid image edit.")
            rotation = transform.get("rotation", 0)
            if isinstance(rotation, bool) or not isinstance(rotation, int) or rotation not in (0, 90, 180, 270):
                raise CaptureError("Rotation must be 0, 90, 180 or 270 degrees.")
            with Image.open(original) as source:
                if source.format not in ("JPEG", "PNG", "WEBP", "HEIF", "HEIC"):
                    raise CaptureError("Use JPEG, PNG, WebP or supported HEIC images.", 415)
                if source.width * source.height > 25_000_000 or min(source.size) < 16:
                    raise CaptureError("Images must have at least 16 pixels per side and at most 25 megapixels.", 413)
                source.load()
                im = ImageOps.exif_transpose(source).convert("RGB")
                original_size = im.size
                if rotation:
                    im = im.rotate(-rotation, expand=True)
                crop = transform.get("crop")
                if crop is not None:
                    if not isinstance(crop, list) or len(crop) != 4 or not all(
                            not isinstance(v, bool) and isinstance(v, (int, float))
                            and 0 <= v <= 1 and math.isfinite(v) for v in crop):
                        raise CaptureError("Select a valid crop rectangle.")
                    x0, y0, x1, y1 = crop
                    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                        raise CaptureError("The crop must stay inside the image.")
                    box = (round(x0*im.width), round(y0*im.height), round(x1*im.width), round(y1*im.height))
                    if min(box[2]-box[0], box[3]-box[1]) < 16:
                        raise CaptureError("The crop is too small. Keep the complete declaration in view.")
                    im = im.crop(box)
                im.save(target)
            hashes[str(original)] = sha256_file(str(original))
            panel = panel_list[index] if index < len(panel_list) else Panel.UNKNOWN
            captures.append(Capture(str(target), panel))
            edit_records.append({"frame": str(target), "original": str(original), "original_size": original_size,
                                 "rotation_clockwise": rotation, "crop": crop, "panel": panel.value})
            # Only measured PDP dimensions are accepted here. No user-supplied exact scale claim.
            if panel is Panel.PDP and geometry:
                meta = {}
                for key in ("pdp_width_mm", "pdp_height_mm"):
                    value = geometry.get(key)
                    if value is not None:
                        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 5000 or not math.isfinite(value):
                            raise CaptureError("Physical panel dimensions must be between 1 and 5,000 mm.")
                        meta[key] = value
                if len(meta) == 1:
                    raise CaptureError("Provide both the width and height of the physical display panel.")
                if meta:
                    target.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception as exc:
        if session.resolve().parent == root:
            shutil.rmtree(session)
        if isinstance(exc, (UnidentifiedImageError, OSError, Image.DecompressionBombError)):
            raise CaptureError("This file could not be decoded as an image. Try a JPEG or PNG export.", 415) from None
        raise
    return captures, hashes, edit_records
