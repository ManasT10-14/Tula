"""The OCR boundary.

Everything downstream depends on `OcrLine`, never on an engine. That is what
lets the same pipeline run against RapidOCR on a CPU server, a quantised
on-device export, a document VLM, or a fixture file in a unit test -- and it is
what will let Path A and Path B be arbitrated against each other later without
the extraction code knowing there are two of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

BBox = tuple[int, int, int, int]


@dataclass
class OcrLine:
    """One recognised line of text with where it sat on the image."""

    text: str
    bbox: BBox
    confidence: float = 1.0
    # Pixel height of the glyphs, not of the detection box. The metrology
    # engine refines this properly; this is the cheap first estimate.
    height_px: float | None = None
    frame: str | None = None
    # Coordinates always refer to the original frame, including after crops,
    # rotation and perspective correction. Scores are model outputs, not a
    # calibrated probability of correctness.
    polygon: list[tuple[float, float]] = field(default_factory=list)
    angle_degrees: float = 0.0
    variants: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    review_required: bool = False
    # Optional pixel-backed price context, distinct from literal OCR. Always
    # needs review; similarity scores are not OCR or extraction probabilities.
    price_hint: dict | None = None

    @property
    def width_px(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def box_height_px(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = "unknown"
    width: int = 0
    height: int = 0
    warnings: list[str] = field(default_factory=list)
    quality: dict = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    passes: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@runtime_checkable
class OcrEngine(Protocol):
    name: str

    def available(self) -> bool:
        """Can this engine actually run here? Never raises."""

    def read(self, image_path: str) -> OcrResult:
        """Recognise text in an image."""
