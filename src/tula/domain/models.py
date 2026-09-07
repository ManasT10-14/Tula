"""Core records.

Two modelling commitments worth stating up front:

1. A `Finding` is immutable once approved. Corrections create a superseding
   finding plus an `Override` row, never an in-place edit -- an enforcement
   record that can be silently rewritten is worthless as evidence.
2. Every physical number is a `Measured`, never a bare float. A millimetre
   without an uncertainty cannot be compared against a statutory limit
   honestly, and this type makes forgetting that impossible.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import (
    AssuranceTier,
    CaptureCompleteness,
    DeclarationClass,
    ExtractionPath,
    Lane,
    PackageClass,
    Panel,
    Script,
    Severity,
    Verdict,
)

BBox = tuple[int, int, int, int]  # x0, y0, x1, y1 in source-frame pixels


def validated_geo(value: Any) -> tuple[float, float] | None:
    """Validate one explicitly captured latitude/longitude pair.

    Keeping this at the domain boundary means CLI, queued and restored records
    cannot bypass the same finite world-coordinate limits as the web form.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Location must contain latitude and longitude.")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError("Location coordinates must be numbers.")
    latitude, longitude = (float(item) for item in value)
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("Location coordinates must be finite numbers.")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Latitude must be -90 to 90 and longitude -180 to 180.")
    return round(latitude, 6), round(longitude, 6)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Measured(BaseModel):
    """A physical quantity with an expanded uncertainty at coverage factor k=2.

    `value` and `uncertainty` are in `unit`. The interval [value-U, value+U] is
    what the conformity decision in `rules.comparators` actually tests -- never
    the point estimate on its own.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    quantity: str  # e.g. "net_quantity.numeral_cap_height"
    value: float
    uncertainty: float = Field(0.0, ge=0)
    unit: str = "mm"
    tier: AssuranceTier = AssuranceTier.C
    sources: list[str] = Field(default_factory=list)
    method: str | None = None

    @property
    def lower(self) -> float:
        return self.value - self.uncertainty

    @property
    def upper(self) -> float:
        return self.value + self.uncertainty

    def render(self) -> str:
        if self.uncertainty <= 0:
            return f"{self.value:.2f} {self.unit} (exact)"
        return (
            f"{self.value:.2f} ± {self.uncertainty:.2f} {self.unit} "
            f"(k=2, Tier {self.tier.value})"
        )


class EvidenceCoverage(BaseModel):
    """What a capture is entitled to prove a *negative* about.

    The distinction this type exists to enforce: "there is no MRP on this
    package" and "I cannot see an MRP in this photograph" are different
    statements, and only the first is a violation. Everything else in the engine
    is three-valued precisely so that an undecided question can stay undecided;
    without a coverage model the presence checks have no way to reach that third
    value, and every unphotographed panel silently becomes an accusation.

    Two independent conditions have to hold before absence can be asserted:

      * **legibility** -- the recogniser actually read the pack. A blurred frame
        that yields no text says nothing about what is printed on it.
      * **coverage** -- the face the declaration would lawfully appear on was in
        frame. Mandatory declarations may be grouped on any panel, so in the
        general case that means the whole package.

    Neither is the assurance tier, which is about millimetres. A Tier A capture
    of one face of a carton is exact and still proves nothing about the other
    five.
    """

    model_config = ConfigDict(frozen=True)

    panels_captured: list[Panel] = Field(default_factory=list)
    completeness: CaptureCompleteness = CaptureCompleteness.NONE
    attested_complete: bool = False  # operator asserted the whole pack is in frame
    frames: int = 0
    lines_read: int = 0
    legible_lines: int = 0
    mean_confidence: float = 0.0
    unreadable_frames: list[str] = Field(default_factory=list)

    # A line at or above this confidence counts as actually read.
    LEGIBLE_CONFIDENCE: ClassVar[float] = 0.55
    # Fewer confident lines than this and the capture failed, rather than the
    # label being bare: a compliant retail pack carries six mandatory
    # declarations, so three legible lines is a deliberately generous floor.
    MIN_LEGIBLE_LINES: ClassVar[int] = 3

    @property
    def is_legible(self) -> bool:
        return not self.unreadable_frames and self.legible_lines >= self.MIN_LEGIBLE_LINES

    @property
    def is_complete(self) -> bool:
        return self.completeness is CaptureCompleteness.COMPLETE

    def can_prove_absence(
        self, provable_from: list[Panel] | None = None
    ) -> tuple[bool, str]:
        """May "not found" be reported as "not declared"?

        `provable_from` names the panels a declaration must lawfully appear on.
        Photographing those is then sufficient -- Rule 7 puts the net quantity on
        the principal display panel, so a legible PDP with no net quantity on it
        is a violation even though five faces were never seen. When the list is
        empty the declaration may sit anywhere, and only a complete capture can
        establish that it sits nowhere.
        """

        if not self.is_legible:
            if self.lines_read == 0:
                return False, (
                    "No text could be recognised on any captured face, so nothing "
                    "can be concluded about what this package declares. Re-capture "
                    "in better light, square to the panel and in focus."
                )
            return False, (
                f"Only {self.legible_lines} line(s) of text were legible across "
                f"{self.frames} frame(s). That is too little to establish that a "
                "declaration is absent rather than merely unreadable."
            )

        if provable_from:
            missing = [p for p in provable_from if p not in self.panels_captured]
            if missing:
                return False, (
                    "This declaration must appear on the "
                    + ", ".join(p.value for p in provable_from)
                    + " panel, which was not captured."
                )
            return True, ""

        if self.is_complete:
            return True, ""

        captured = ", ".join(p.value for p in self.panels_captured) or "nothing"
        return False, (
            f"Only the {captured} panel was captured. A mandatory declaration may "
            "lawfully be grouped on any face of the package, so its absence cannot "
            "be established without the whole package in evidence. Capture the "
            "remaining faces, or confirm that every printed face is in frame."
        )

    @classmethod
    def assumed_complete(cls) -> EvidenceCoverage:
        """Coverage for inputs that are complete by construction.

        Vector artwork and hand-built test fixtures carry the whole declaration
        set by definition; there is no unphotographed face to worry about.
        """
        return cls(
            panels_captured=[Panel.PDP],
            completeness=CaptureCompleteness.COMPLETE,
            attested_complete=True,
            frames=1,
            lines_read=99,
            legible_lines=99,
            mean_confidence=1.0,
        )


class TextSpan(BaseModel):
    """One recognised line of text, before it means anything."""

    text: str
    bbox: BBox | None = None
    confidence: float = 0.0
    frame: str | None = None
    panel: Panel = Panel.UNKNOWN
    script: Script = Script.LATIN
    height_px: float | None = None


class DeclarationSource(BaseModel):
    """A contributing text region in the retained working image's pixels."""

    frame: str | None = None
    image_index: int | None = None
    image_width: int | None = None
    image_height: int | None = None
    panel: Panel = Panel.UNKNOWN
    bbox: BBox | None = None
    text: str = ""
    ocr_confidence: float | None = Field(None, ge=0, le=1)
    method: str | None = None


class DeclarationProvenance(BaseModel):
    """Scores describe machine observations, never an officer's correctness."""

    method: str = "not_recorded"
    status: str = "not_recorded"
    sources: list[DeclarationSource] = Field(default_factory=list)
    ocr_confidence: float | None = Field(None, ge=0, le=1)
    extraction_confidence: float | None = Field(None, ge=0, le=1)
    score_basis: str = "not_recorded"
    original_transcription: str | None = None
    original_sources: list[DeclarationSource] = Field(default_factory=list)
    original_ocr_confidence: float | None = Field(None, ge=0, le=1)


class Declaration(BaseModel):
    """A mandatory declaration, located and normalised.

    `raw` is what was printed. `norm` is the canonical interpretation, and is
    the only thing rules are allowed to reason about -- so that "Rs.100/-",
    "MRP 100.00" and "₹100" collapse to one comparable value.
    """

    klass: DeclarationClass
    raw: str
    norm: dict[str, Any] = Field(default_factory=dict)
    bbox: BBox | None = None
    frame: str | None = None
    panel: Panel = Panel.UNKNOWN
    scripts: list[Script] = Field(default_factory=list)
    confidence: float = 0.0
    path: ExtractionPath = ExtractionPath.CLASSICAL
    # every value this declaration was seen with, across paths and frames --
    # kept so dual-MRP and path disagreement are both detectable downstream
    variants: list[str] = Field(default_factory=list)
    # Additive: legacy JSON can be read without rewriting its retained bytes.
    # confidence and norm metadata remain for existing consumers; this is the
    # explicit provenance contract for new records and corrected transcriptions.
    provenance: DeclarationProvenance | None = None


class PackageFacts(BaseModel):
    """What we believe about the package itself, as opposed to its text."""

    klass: PackageClass = PackageClass.RETAIL
    is_blown_formed: bool = False
    pdp_area_cm2: Measured | None = None
    capacity_value: float | None = None  # in g or ml, whichever applies
    capacity_unit: str | None = None
    gtin: str | None = None
    is_imported: bool = False
    panels_captured: list[Panel] = Field(default_factory=list)
    exemptions: list[str] = Field(default_factory=list)  # e.g. ["R26(a)"]
    legal_context: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_exempt(self) -> bool:
        return bool(self.exemptions)


class Citation(BaseModel):
    act: str = "Legal Metrology Act, 2009"
    rules: str = "Legal Metrology (Packaged Commodities) Rules, 2011"
    clause: str = ""
    text: str = ""  # verbatim statutory text, quoted in the report


class Finding(BaseModel):
    """The atomic compliance result. This is the product's output contract."""

    model_config = ConfigDict(frozen=True)

    finding_id: str
    rule_id: str
    rules_version: str
    citation: Citation
    declaration: DeclarationClass | None = None
    verdict: Verdict
    severity: Severity = Severity.MAJOR
    message: str = ""
    detail: str = ""
    measured: Measured | None = None
    threshold: float | None = None
    threshold_basis: str | None = None
    extracted: str | None = None
    evidence: list[str] = Field(default_factory=list)
    penalty_ref: str | None = None
    confidence: float = 1.0
    tier: AssuranceTier | None = None


class ScaleEstimate(BaseModel):
    """One independent opinion about mm-per-pixel at the label plane."""

    model_config = ConfigDict(allow_inf_nan=False)
    source: str  # aruco_card | arcore_depth | mono_metric | geometry_prior | artwork
    mm_per_px: float = Field(gt=0)
    sigma: float = Field(ge=0)  # 1-sigma, same units
    tier: AssuranceTier
    detail: str = ""
    frame: str | None = None


class Scan(BaseModel):
    """One capture session -- the unit of evidence."""

    scan_id: str
    lane: Lane = Lane.FIELD
    captured_at: datetime = Field(default_factory=_utcnow)
    packing_date: date | None = None  # drives which rule version applies
    frames: list[str] = Field(default_factory=list)
    frame_hashes: dict[str, str] = Field(default_factory=dict)
    original_frame_hashes: dict[str, str] = Field(default_factory=dict)
    geo: tuple[float, float] | None = None
    device: str | None = None
    operator: str | None = None
    tier: AssuranceTier = AssuranceTier.C
    scales: list[ScaleEstimate] = Field(default_factory=list)
    # what this capture may prove a negative about -- see EvidenceCoverage
    coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    notes: str = ""
    inspector_id: str | None = None
    source: str = "inspection"
    parent_scan_id: str | None = None
    parent_revision: int | None = Field(None, ge=0)
    parent_record_sha256: str | None = None
    rescan_target: str = ""
    region: str = ""
    image_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    capture_edits: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("geo", mode="before")
    @classmethod
    def coordinates_are_valid(cls, value):
        return validated_geo(value)


class ReviewDecision(BaseModel):
    finding_id: str
    verdict: Verdict
    reason: str
    actor_id: str
    actor_name: str
    created_at: datetime = Field(default_factory=_utcnow)


class ReviewState(BaseModel):
    revision: int = 0
    status: str = "draft"
    decisions: dict[str, ReviewDecision] = Field(default_factory=dict)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    comments: list[dict[str, Any]] = Field(default_factory=list)
    submitted_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_reason: str = ""


class Analysis(BaseModel):
    """Everything one scan produced. Serialises straight into the report."""

    scan: Scan
    package: PackageFacts
    declarations: dict[DeclarationClass, Declaration] = Field(default_factory=dict)
    spans: list[TextSpan] = Field(default_factory=list)
    measurements: dict[str, Measured] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    rules_version: str = ""
    engine: str = ""
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    intelligence: dict[str, Any] = Field(default_factory=dict)
    review: ReviewState = Field(default_factory=ReviewState)

    @property
    def verified_violations(self) -> list[Finding]:
        return [f for f in self.findings if f.finding_id in self.review.decisions
                and self.review.decisions[f.finding_id].verdict is Verdict.VIOLATION]

    @property
    def pending_review(self) -> list[Finding]:
        uncertain = (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED, Verdict.ADVISORY)
        return [f for f in self.findings if (
            self.review.decisions[f.finding_id].verdict in uncertain
            if f.finding_id in self.review.decisions
            else f.verdict in (Verdict.VIOLATION, *uncertain))]

    @property
    def product_status(self) -> str:
        if self.review.status != "approved":
            return "NEEDS_REVIEW"
        if self.verified_violations:
            return "NON_COMPLIANT"
        if self.pending_review or not self.findings:
            return "NEEDS_REVIEW"
        if self.overall in (Verdict.EXEMPT, Verdict.NOT_APPLICABLE):
            return self.overall.value
        return "COMPLIANT"

    # ---- summary helpers used by the report and the dashboard ----

    @property
    def violations(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.VIOLATION]

    @property
    def advisories(self) -> list[Finding]:
        """Non-conformities the lane or the evidence may not convict on.

        Kept separate from `inconclusive` because they mean different things to
        the officer reading the report: an advisory says "this pack looks wrong,
        go and seize it", an inconclusive says "the evidence does not decide".
        """
        return [f for f in self.findings if f.verdict is Verdict.ADVISORY]

    @property
    def inconclusive(self) -> list[Finding]:
        return [
            f
            for f in self.findings
            if f.verdict in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)
        ]

    @property
    def needs_review(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict.needs_human]

    @property
    def passed(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.PASS]

    @property
    def overall(self) -> Verdict:
        if self.package.klass is not PackageClass.RETAIL:
            return Verdict.NOT_APPLICABLE
        if self.package.is_exempt and not self.violations:
            return Verdict.EXEMPT
        if self.violations:
            return Verdict.VIOLATION
        if self.advisories:
            return Verdict.ADVISORY
        if self.inconclusive:
            return Verdict.INCONCLUSIVE
        if not self.findings or all(f.verdict is Verdict.NOT_APPLICABLE for f in self.findings):
            return Verdict.NOT_APPLICABLE
        return Verdict.PASS

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {v.value: 0 for v in Verdict}
        for f in self.findings:
            out[f.verdict.value] += 1
        return out


class Override(BaseModel):
    """Officer disagreement. Audit trail and training signal in one record."""

    finding_id: str
    officer: str
    before: Verdict
    after: Verdict
    reason: str
    created_at: datetime = Field(default_factory=_utcnow)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text: str, n: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]
