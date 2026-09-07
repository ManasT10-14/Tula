"""Temporary, authenticated pixel-quality feedback before evidence submission."""
from __future__ import annotations

import base64
import io
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import Form, HTTPException, Request, UploadFile
from PIL import Image

from ..imaging.quality import assess_quality
from ..security.web import require_roles
from ..services.capture import CaptureError, receive

_SLOTS = threading.BoundedSemaphore(2)
_MAX_BYTES = 25 * 1024 * 1024


def install_capture_quality(web):
    """Install against web.app and web.ROOT; no repository writes or saved previews."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass  # receive() reports unsupported HEIC without pretending to decode it.
    root = Path(web.ROOT).resolve() / "data" / "capture-previews"
    root.mkdir(parents=True, exist_ok=True)
    inspector = require_roles("inspector", "supervisor", "admin")

    @web.app.post("/v1/capture/preview")
    def preview(request: Request, file: UploadFile, edit: str = Form("{}")):
        """Return heuristics and a browser-readable preview, then remove temporary files.

        The client must still submit the original file to /v1/inspections.
        This endpoint does not create an inspection or establish label absence.
        """
        inspector(request)
        if file.size is not None and file.size > _MAX_BYTES:
            raise HTTPException(413, "Each image must be 25 MB or smaller.")
        try:
            if len(edit) > 1000:
                raise ValueError("Image edit data is too long.")
            transform = json.loads(edit)
            if not isinstance(transform, dict) or set(transform) - {"rotation", "crop"}:
                raise ValueError("Provide only image rotation and crop coordinates.")
            rotation = transform.get("rotation", 0)
            if type(rotation) is not int or rotation not in (0, 90, 180, 270):
                raise ValueError("Rotation must be 0, 90, 180 or 270 degrees.")
            crop = transform.get("crop")
            if crop is not None and (not isinstance(crop, list) or len(crop) != 4 or not all(
                    type(value) in (int, float) and 0 <= value <= 1 for value in crop)):
                raise ValueError("Crop coordinates must be four numbers between zero and one.")
            transforms = json.dumps([transform], allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        if not _SLOTS.acquire(blocking=False):
            raise HTTPException(429, "Image checks are busy. Retry this preview shortly.", headers={"Retry-After": "2"})
        try:
            # TemporaryDirectory owns only this request's directory. receive()
            # applies the same byte/pixel/format/crop limits as final capture.
            with TemporaryDirectory(prefix="preview-", dir=root) as temporary:
                captures, _, records = receive([file], temporary, edits=transforms)
                quality = assess_quality(captures[0].path, crop=crop)
                with Image.open(captures[0].path) as working:
                    working.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    encoded = io.BytesIO()
                    working.save(encoded, format="JPEG", quality=88)
                    preview_size = list(working.size)
                result = {
                    "quality": quality,
                    "original_size": list(records[0]["original_size"]),
                    "working_size": [quality["width"], quality["height"]],
                    "preview_size": preview_size,
                    "preview": "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii"),
                    "retained": False,
                }
            return result
        except CaptureError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        finally:
            _SLOTS.release()
