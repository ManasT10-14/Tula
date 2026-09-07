"""Concrete OCR engines and the registry that picks one.

Default is RapidOCR: the PP-OCR models pre-exported to ONNX, which is the right
call given inference must assume no GPU. It is pip-installable on Windows,
needs no Paddle runtime, and the same ONNX artefacts are what an on-device
build would ship.

`FixtureEngine` is not a toy. Model weights download on first use and CI has no
network, so a deterministic engine that reads a text sidecar is what keeps the
rules engine, the normalisers and the report testable without any of that.
"""

from __future__ import annotations

import math
import os
import threading
import time
from functools import lru_cache
from pathlib import Path

from .base import OcrLine, OcrResult


class FixtureEngine:
    """Reads `<image>.txt` beside the image, one line of label text per row.

    Row formats, all optional-prefix:

        text
        x0,y0,x1,y1|text                 geometry, cap height inferred
        x0,y0,x1,y1,cap_height_px|text   geometry with exact cap height

    The five-field form is what `tula.labgen` writes, and it is what makes this
    engine a *perfect recogniser* rather than an approximation: the cap height
    is the one the renderer actually drew, so a measurement error under this
    engine is a bug in the metrology, never in the OCR.
    """

    name = "fixture"
    # Cap height as a fraction of a detection box, used only when the sidecar
    # does not state it. Real detectors box the ascender-to-descender span.
    CAP_FRACTION = 0.62

    def available(self) -> bool:
        return True

    def read(self, image_path: str) -> OcrResult:
        sidecar = Path(image_path).with_suffix(".txt")
        if not sidecar.exists():
            return OcrResult(
                engine=self.name,
                warnings=[f"no fixture sidecar at {sidecar.name}; recognised nothing"],
            )

        lines: list[OcrLine] = []
        y = 0
        for row in sidecar.read_text(encoding="utf-8").splitlines():
            row = row.rstrip()
            if not row or row.startswith("#"):
                continue

            cap_px: float | None = None
            if "|" in row:
                geom, _, text = row.partition("|")
                parts = geom.split(",")
                try:
                    if len(parts) == 5:
                        x0, y0, x1, y1 = (int(float(v)) for v in parts[:4])
                        cap_px = float(parts[4])
                    else:
                        x0, y0, x1, y1 = (int(float(v)) for v in parts[:4])
                except (ValueError, IndexError):
                    x0, y0, x1, y1 = 0, y, 400, y + 24
                    text = row
            else:
                text, x0, y0, x1, y1 = row, 0, y, 400, y + 24

            lines.append(
                OcrLine(
                    text=text.strip(),
                    bbox=(x0, y0, x1, y1),
                    confidence=0.99,
                    height_px=cap_px if cap_px is not None
                    else float(y1 - y0) * self.CAP_FRACTION,
                )
            )
            y = max(y + 30, y1 + 6)

        return OcrResult(lines=lines, engine=self.name)


class RapidOcrEngine:
    """PP-OCR detection + recognition via ONNX Runtime, CPU only."""

    name = "rapidocr"

    def __init__(self) -> None:
        self._reader = None
        self._lock = threading.RLock()

    def _load(self):
        if self._reader is not None:
            return self._reader
        try:  # package moved namespace between major versions
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError:
            try:
                from rapidocr import RapidOCR  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "rapidocr is not installed; run `pip install rapidocr-onnxruntime`"
                ) from exc
        self._reader = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        return self._reader

    def available(self) -> bool:
        try:
            with self._lock:
                self._load()
            return True
        except Exception:  # noqa: BLE001 - readiness probe for third-party OCR initialization
            return False

    def _recognize(self, image, *, observe_orientation=False, automatic_orientation=True):
        """Observe actual crop rotations under the cached reader's existing lock.

        The callback is restored even if inference fails. Its crop indexes are
        internal detector ordering, not asserted original-image coordinates;
        recognized candidate boxes remain the source-coordinate authority.
        """
        with self._lock:
            reader = self._load()
            if not automatic_orientation:
                return reader(image, use_cls=False), {"automatic_crop_orientation": False}
            classifier = getattr(reader, "text_cls", None)
            if not observe_orientation or not callable(classifier) or not getattr(reader, "use_cls", False):
                return reader(image), {}
            try:
                threshold = float(classifier.cls_thresh)
            except (AttributeError, TypeError, ValueError):
                return reader(image), {"orientation_observation": "unavailable"}
            if not math.isfinite(threshold):
                return reader(image), {"orientation_observation": "unavailable"}
            observation = {"threshold": threshold, "decisions": [],
                           "coordinate_scope": "internal detector crops; use candidate boxes for original-image evidence"}

            def traced(images):
                result = classifier(images)
                try:
                    for index, (label, score) in enumerate(result[1]):
                        score = float(score)
                        if math.isfinite(score):
                            observation["decisions"].append({"crop_index": index,
                                "label": str(label), "score": score,
                                "rotated_180": "180" in str(label) and score > threshold})
                except (TypeError, ValueError, IndexError):
                    observation["status"] = "unavailable"
                return result

            try:
                reader.text_cls = traced
            except (AttributeError, TypeError):
                return reader(image), {"orientation_observation": "unavailable"}
            try:
                raw = reader(image)
            finally:
                reader.text_cls = classifier
            return raw, {"orientation_classification": observation}

    def read(self, image_path: str) -> OcrResult:
        """Recognise bounded preprocessing passes; retain all candidate evidence."""
        import cv2
        import numpy as np

        from ..imaging.quality import assess_quality
        from .arbitrate import arbitrate, candidate_dict
        from .auxiliary import read_tesseract
        from .currency import SLASH_PRICE, attach_price_hint, price_regions
        from .preprocess import (
            Variant,
            contrast_variant,
            fit_variant,
            perspective_variant,
            reconnect_ink_regions,
            reconnect_ink_variant,
            rotate_variant,
            targeted_variants,
        )

        started = time.perf_counter()
        image = (cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
                 if Path(image_path).is_file() else None)
        # Preserve the adapter boundary for readers supplied by callers (and
        # tests). A real missing path still raises inside the real recognizer.
        if image is None:
            with self._lock:
                raw = self._load()(image_path)
            return OcrResult(lines=self._decode(raw), engine=self.name)
        height, width = image.shape[:2]
        quality = assess_quality(image)
        max_passes = min(10, max(2, int(os.getenv("TULA_OCR_MAX_PASSES", "6"))))
        budget = min(90.0, max(3.0, float(os.getenv("TULA_OCR_BUDGET_SECONDS", "25"))))
        candidates, passes, warnings = [], [], []

        def run(variant, *, observe_orientation=False, automatic_orientation=True):
            if len(passes) >= max_passes or (passes and time.perf_counter() - started > budget):
                return False
            tick = time.perf_counter()
            try:
                raw, observation = self._recognize(variant.image,
                    observe_orientation=observe_orientation, automatic_orientation=automatic_orientation)
                lines = self._decode(raw, variant=variant, width=width, height=height)
                candidates.extend(lines)
                passes.append({"variant": variant.name, "status": "completed", "lines": len(lines),
                               "elapsed_ms": round((time.perf_counter() - tick) * 1000),
                               "input_width": variant.image.shape[1], "input_height": variant.image.shape[0],
                               **observation})
            except Exception as exc:  # noqa: BLE001 - preserve partial recognition and disclose failure
                passes.append({"variant": variant.name, "status": "failed", "lines": 0,
                               "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                warnings.append(f"OCR pass {variant.name} failed ({type(exc).__name__}); this region requires review.")
            return True

        original = fit_variant(image)
        run(original, observe_orientation=True)
        base_lines = list(candidates)
        run(contrast_variant(original))
        reconnect_regions = reconnect_ink_regions(image, base_lines)
        if reconnect_regions or any(issue["code"] == "fragmented_ink" for issue in quality["issues"]):
            run(reconnect_ink_variant(image))
        # A confidently wrong 180-degree classifier decision can reverse every
        # reading of upright mixed-script/numeric rows. Cross-check only when
        # an actual flip coincides with weak/conflicting numeric recognition.
        # The alternate uses identical image pixels; it is not an orientation
        # verdict. Both readings enter the ordinary source-box arbitration.
        observed = passes[0].get("orientation_classification", {}).get("decisions", [])
        numeric_readings, _ = arbitrate(candidates)
        orientation_probe = None
        if (any(decision["rotated_180"] for decision in observed)
                and any(sum(char.isdigit() for char in line.text) >= 2
                        and (line.confidence < .85 or line.review_required) for line in numeric_readings)):
            alternate = Variant("unrotated_candidate", original.image, original.to_original)
            attempted = run(alternate, automatic_orientation=False)
            orientation_probe = {"trigger": "observed 180-degree crop rotation with weak or conflicting numeric text",
                "status": passes[-1]["status"] if attempted else "budget_skipped",
                "parent_variant": "original", "candidate_variant": alternate.name}
            if not attempted:
                warnings.append("An uncertain numeric region followed automatic crop rotation, but the alternative orientation could not be checked within the OCR budget. Verify the original marking or capture a close-up.")
        if base_lines:
            import statistics
            angle = statistics.median(l.angle_degrees for l in base_lines)
            # OCR crop classifiers can rotate text but not the detection image.
            # An explicit image rotation is useful for long vertical labels.
            vertical = sum(l.box_height_px > l.width_px * 2 for l in base_lines) > len(base_lines) / 2
            if vertical:
                run(rotate_variant(original, 90))
            elif 2 < abs(angle) < 40:
                run(rotate_variant(original, angle))
        if sum(l.confidence >= 0.55 for l in candidates) < 3:
            run(rotate_variant(original, 90))
            run(rotate_variant(original, 270))
        if len(passes) < max_passes and time.perf_counter() - started < budget:
            tick = time.perf_counter()
            try:
                secondary = read_tesseract(original, width, height,
                                           timeout=min(8, max(1, budget - (tick - started))))
                if secondary is not None:
                    extra, languages, missing = secondary
                    candidates.extend(extra)
                    passes.append({"variant": "tesseract", "status": "completed", "lines": len(extra),
                                   "languages": languages, "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                    if missing:
                        warnings.append(f"Secondary OCR language data unavailable: {', '.join(missing)}.")
                else:
                    warnings.append("Optional Tesseract OCR is unavailable or disabled; only the RapidOCR model was used. Install Tesseract with eng and hin data for a second model and Hindi recognition.")
            except Exception as exc:  # noqa: BLE001 - secondary OCR never discards the primary result
                passes.append({"variant": "tesseract", "status": "failed", "lines": 0,
                               "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                warnings.append(f"Secondary OCR unavailable ({type(exc).__name__}); compare uncertain text with the original image.")
        perspective = perspective_variant(original)
        if perspective is not None:
            run(perspective)
        chosen, _ = arbitrate(candidates)
        price_hints = []
        if len(passes) < max_passes and time.perf_counter() - started < budget:
            for anchor, variant, hint in price_regions(image, chosen):
                hint["anchor_bbox"] = list(anchor.bbox)
                hint["readings"] = ([{"text": anchor.text, "amount_text": hint["amount_text"],
                                     "bbox": list(anchor.bbox), "confidence": anchor.confidence,
                                     "variant": "selected_ocr"}] if hint["amount_text"] else [])
                price_hints.append(hint)
                if len(passes) >= max_passes or time.perf_counter() - started >= budget:
                    break
                tick = time.perf_counter()
                try:
                    secondary = read_tesseract(variant, width, height, psm=7,
                        timeout=min(5, max(1, budget - (tick - started))))
                    if secondary is not None:
                        extra, languages, _missing = secondary
                        candidates.extend(extra)
                        passes.append({"variant": f"tesseract_{variant.name}", "status": "completed",
                                       "lines": len(extra), "languages": languages,
                                       "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                        for line in extra:
                            amount = SLASH_PRICE.search(line.text)
                            if amount:
                                hint["readings"].append({"text": line.text, "amount_text": amount[1],
                                    "bbox": list(line.bbox), "confidence": line.confidence,
                                    "variant": f"tesseract_{variant.name}"})
                except Exception as exc:  # noqa: BLE001 - a context probe cannot discard primary OCR
                    passes.append({"variant": f"tesseract_{variant.name}", "status": "failed",
                                   "lines": 0, "elapsed_ms": round((time.perf_counter() - tick) * 1000)})
                    warnings.append(f"Price context recognition failed ({type(exc).__name__}); verify the original price.")
        for variant in targeted_variants(image, chosen):
            if not run(variant):
                break
        lines, conflicts = arbitrate(candidates)
        from .arbitrate import overlap
        for line in lines:
            for hint in price_hints:
                if overlap(line.bbox, hint["anchor_bbox"])[1] > .5:
                    attach_price_hint(line, hint)
        # Preserve reading order across transformed runs using original-frame
        # orientation, while every evidence crop stays in the original frame.
        vertical = sum(l.box_height_px > l.width_px * 2 for l in lines) > len(lines) / 2
        lines.sort(key=(lambda l: (round(l.centre[0] / 8), -l.centre[1])) if vertical else
                   (lambda l: (round(l.centre[1] / 8), l.centre[0])))
        quality = assess_quality(image, text_boxes=[l.bbox for l in lines])
        if orientation_probe:
            quality["orientation_alternative"] = orientation_probe
        if reconnect_regions:
            quality["ink_reconnect_regions"] = reconnect_regions
            quality["issues"].append({"code": "porous_numeric_ink", "severity": "warning",
                "message": "Detected numeric printing contains small gaps that can change recognized digits or punctuation.",
                "action": "Compare every digit and date separator with the original image; capture a closer stamped region if needed.",
                "bbox": reconnect_regions[0]["bbox"]})
            quality["status"] = "review"
        if price_hints:
            quality["price_context_candidates"] = price_hints
            warnings.append("A nearby ink shape may be a rupee symbol. Price context candidates require source-image review; they are not verified MRP declarations.")
        # Fragmented print can yield a confident but wrong digit string from
        # a single model. Require independent-model agreement for critical
        # stamped values while keeping every proposed value visible to review.
        fragmented = any(i["code"] == "fragmented_ink" for i in quality["issues"])
        if fragmented or reconnect_regions:
            for line in lines:
                affected = fragmented or any(overlap(line.bbox, r["bbox"])[1] > .5
                                             for r in reconnect_regions)
                literal = "".join(line.text.casefold().split())
                agreeing = [c for c in line.alternatives
                            if "".join(c["text"].casefold().split()) == literal and c["confidence"] >= .55]
                independent = (any(c["variant"].startswith("tesseract") for c in agreeing)
                               and any(not c["variant"].startswith("tesseract") for c in agreeing))
                if affected and any(c.isdigit() for c in line.text) and not independent:
                    line.review_required = True
                    line.confidence = min(line.confidence, 0.54)
        if conflicts:
            disagreements = sum(conflict.get("kind") != "date_token_uncertainty" for conflict in conflicts)
            uncertain_tokens = len(conflicts) - disagreements
            if disagreements:
                warnings.append(f"{disagreements} text region(s) have conflicting OCR candidates. Verify them against the image or capture a close-up; they cannot establish absence or a violation.")
            if uncertain_tokens:
                warnings.append(f"{uncertain_tokens} text region(s) contain uncertain date-like tokens. Verify the printed letters, digits and separators; no corrected spelling or date meaning has been inferred.")
        if not candidates:
            warnings.append("No text could be read. Focus on the printed declarations and capture a closer, evenly lit image.")
        if time.perf_counter() - started > budget:
            warnings.append("OCR time budget reached; additional enhancement passes were skipped.")
        warnings.extend(f"{i['message']} {i['action']}" for i in quality["issues"])
        return OcrResult(lines=lines, engine=self.name, width=width, height=height,
                         warnings=warnings, quality=quality, conflicts=conflicts,
                         candidates=[candidate_dict(l) for l in candidates], passes=passes)

    @staticmethod
    def _decode(raw, *, variant=None, width=0, height=0) -> list[OcrLine]:
        # RapidOCR returns (results, elapsed) across versions, or an object
        # with `.boxes` / `.txts` / `.scores` in the 2.x line.
        if isinstance(raw, tuple):
            results = raw[0]
        else:
            boxes = getattr(raw, "boxes", None)
            texts = getattr(raw, "txts", None)
            scores = getattr(raw, "scores", None)
            results = list(zip(boxes, texts, scores)) if boxes is not None and texts is not None and scores is not None else None
        lines: list[OcrLine] = []
        positioned = []
        if isinstance(results, list):
            for item in results or []:
                box, text, score = item[0], item[1], float(item[2])
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                if variant is not None:
                    from .preprocess import map_polygon
                    box = map_polygon(box, variant.to_original, width, height)
                    xs, ys = [p[0] for p in box], [p[1] for p in box]
                bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                # Use the shorter pair of box edges as the text height: a
                # rotated line's axis-aligned bbox badly overstates it.
                edge = min(
                    ((box[0][0] - box[3][0]) ** 2 + (box[0][1] - box[3][1]) ** 2) ** 0.5,
                    ((box[1][0] - box[2][0]) ** 2 + (box[1][1] - box[2][1]) ** 2) ** 0.5,
                )
                line = OcrLine(text=str(text).strip(), bbox=bbox, confidence=score,
                               height_px=edge * 0.62 if edge else None,
                               polygon=[(float(p[0]), float(p[1])) for p in box],
                               angle_degrees=math.degrees(math.atan2(box[1][1]-box[0][1], box[1][0]-box[0][0])),
                               variants=[variant.name if variant is not None else "original"])
                # Use the detected reading orientation to order rotated labels.
                # Original coordinates remain unchanged for evidence crops.
                dx, dy = float(box[3][0]-box[0][0]), float(box[3][1]-box[0][1])
                length = math.hypot(dx, dy) or 1
                positioned.append((line, dx/length, dy/length))
        if positioned:
            import statistics
            vx = statistics.median(p[1] for p in positioned)
            vy = statistics.median(p[2] for p in positioned)
            vertical = sum(p[0].box_height_px > p[0].width_px * 2 for p in positioned) > len(positioned) / 2
            if vertical:
                # PP-OCR orders the vertices geometrically, even after its
                # classifier rotates text crops. For vertical labels the short
                # edge is therefore horizontal: group columns, not y centres.
                lines = sorted((p[0] for p in positioned), key=lambda l: (round(l.centre[0]/8), -l.centre[1]))
            else:
                lines = sorted((p[0] for p in positioned), key=lambda l: (round((l.centre[0]*vx+l.centre[1]*vy)/8), l.centre[0]*vy-l.centre[1]*vx))

        return lines


_ENGINES: dict[str, type] = {
    "fixture": FixtureEngine,
    "rapidocr": RapidOcrEngine,
}


def get_engine(name: str | None = None):
    """Pick an engine by name, environment, or availability.

    Auto uses real OCR. Recognition failures become visible analysis warnings;
    fixture recognition must be selected explicitly.
    """
    requested = name or os.environ.get("TULA_OCR", "auto")
    return _get_engine(requested)


@lru_cache(maxsize=3)
def _get_engine(requested: str):
    if requested not in (*_ENGINES, "auto"):
        raise ValueError(f"Unknown OCR engine {requested!r}; choose auto, rapidocr or fixture")

    if requested != "auto":
        engine = _ENGINES[requested]()
        return engine

    # A failed recognizer must be visible as an OCR warning, never silently
    # replaced by synthetic sidecar text for a real photograph.
    return _get_engine("rapidocr")
