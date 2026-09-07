"""Optional second recognition model via an installed Tesseract executable.

No uploads, shell commands or background process are involved. Missing language
packs are disclosed, and subprocess work has a hard timeout. TSV word scores
are retained conservatively as the minimum score of each reconstructed line.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
from functools import lru_cache

import cv2

from .base import OcrLine
from .preprocess import map_polygon


@lru_cache(maxsize=1)
def tesseract_config():
    executable = shutil.which("tesseract")
    if not executable or os.getenv("TULA_TESSERACT", "auto").lower() in {"off", "0", "false"}:
        return None
    completed = subprocess.run([executable, "--list-langs"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=5, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    available = set(completed.stdout.splitlines()[1:])
    requested = os.getenv("TULA_TESSERACT_LANGS", "eng+hin")
    if not re.fullmatch(r"[a-zA-Z_]+(?:\+[a-zA-Z_]+)*", requested):
        raise ValueError("TULA_TESSERACT_LANGS must contain language codes joined with +")
    usable = [lang for lang in requested.split("+") if lang in available]
    if not usable:
        return None
    return executable, "+".join(usable), sorted(set(requested.split("+")) - available)


def read_tesseract(variant, width, height, *, timeout=8, psm=1):
    if psm not in {1, 7}:
        raise ValueError("Only full-page or single-line secondary recognition is supported")
    config = tesseract_config()
    if config is None:
        return None
    executable, languages, missing = config
    encoded, payload = cv2.imencode(".png", variant.image)
    if not encoded:
        raise ValueError("Could not encode image for secondary recognition")
    completed = subprocess.run([executable, "stdin", "stdout", "-l", languages, "--psm", str(psm), "tsv"],
                               input=payload.tobytes(), capture_output=True, timeout=timeout, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if completed.returncode:
        raise RuntimeError("Secondary OCR model failed; verify installed Tesseract language data")
    lines = decode_tsv(completed.stdout.decode("utf-8", errors="replace"), variant, width, height,
                       variant_name="tesseract" if psm == 1 else f"tesseract_{variant.name}")
    return lines, languages, missing


def decode_tsv(text, variant, width, height, *, variant_name="tesseract"):
    grouped = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            confidence = float(row["conf"]) / 100
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        except (ValueError, KeyError):
            continue
        if confidence < 0:
            continue
        key = tuple(row.get(k) for k in ("page_num", "block_num", "par_num", "line_num"))
        grouped.setdefault(key, []).append((row["text"], confidence, (x, y, x + w, y + h)))
    lines = []
    for words in grouped.values():
        x0, y0 = min(w[2][0] for w in words), min(w[2][1] for w in words)
        x1, y1 = max(w[2][2] for w in words), max(w[2][3] for w in words)
        polygon = map_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], variant.to_original, width, height)
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        bbox = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        lines.append(OcrLine(" ".join(w[0] for w in words), bbox,
                             confidence=min(w[1] for w in words), polygon=polygon,
                             variants=[variant_name],
                             angle_degrees=90.0 if bbox[3] - bbox[1] > 2 * (bbox[2] - bbox[0]) else 0.0,
                             height_px=min(bbox[3] - bbox[1], bbox[2] - bbox[0]) * .62))
    return lines
