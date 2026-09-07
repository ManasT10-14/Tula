"""Controlled vocabularies.

Every string that crosses a module boundary or lands in a report is defined
here, because report text and rule packs both key off these values.
"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    """Outcome of evaluating one rule against one package.

    The set is deliberately larger than pass/fail. An enforcement system that
    can only say "violation" or "compliant" is forced to guess, and a wrong
    accusation costs far more than a deferred one.
    """

    PASS = "PASS"
    VIOLATION = "VIOLATION"
    ADVISORY = "ADVISORY"  # non-conformity indicated, but this lane may not convict
    INCONCLUSIVE = "INCONCLUSIVE"  # measurement straddles the limit -> re-capture
    NOT_APPLICABLE = "NOT_APPLICABLE"  # rule not in force, or package out of scope
    EXEMPT = "EXEMPT"  # Rule 26 or Rule 3 relieves the package of this duty
    UNVERIFIED = "UNVERIFIED"  # needed an input we could not obtain (e.g. offline)

    @property
    def is_adverse(self) -> bool:
        return self is Verdict.VIOLATION

    @property
    def needs_human(self) -> bool:
        return self in (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED, Verdict.ADVISORY)


class Severity(str, Enum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class AssuranceTier(str, Enum):
    """How well we know a physical measurement.

    A -- read from vector artwork. Exact; no estimation of any kind.
    B -- fiducial marker or device depth. Legally usable, sigma ~0.05-0.25 mm.
    C -- monocular estimate. Advisory only; may never sustain a violation.
    """

    A = "A"
    B = "B"
    C = "C"

    @property
    def rank(self) -> int:
        return {"A": 3, "B": 2, "C": 1}[self.value]

    def satisfies(self, minimum: AssuranceTier) -> bool:
        return self.rank >= minimum.rank


class DeclarationClass(str, Enum):
    """The mandatory declarations of Rule 6(1), as machine-addressable keys."""

    MANUFACTURER = "manufacturer"  # R6(1)(a)
    GENERIC_NAME = "generic_name"  # R6(1)(b)
    NET_QUANTITY = "net_quantity"  # R6(1)(c)
    DATE_OF_PACKING = "date_of_packing"  # R6(1)(d)
    RETAIL_SALE_PRICE = "retail_sale_price"  # R6(1)(e)
    CONSUMER_CARE = "consumer_care"  # R6(1)(f)
    UNIT_SALE_PRICE = "unit_sale_price"  # R6(1)(f), as amended 2022
    COUNTRY_OF_ORIGIN = "country_of_origin"  # R6 proviso / imports
    BRAND = "brand"  # not mandatory, but needed to test R6(1)(b)


class PackageClass(str, Enum):
    RETAIL = "retail"
    WHOLESALE = "wholesale"
    INSTITUTIONAL = "institutional"
    INDUSTRIAL = "industrial"
    UNKNOWN = "unknown"


class Panel(str, Enum):
    """Face of the package a declaration was found on.

    Rules 7 and 9(2) are spatial. Without panel assignment they are undecidable,
    which is why single-photo pipelines silently skip them.
    """

    PDP = "pdp"  # principal display panel
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    UNKNOWN = "unknown"


class Script(str, Enum):
    LATIN = "latin"
    DEVANAGARI = "devanagari"
    OTHER = "other"
    UNREADABLE = "unreadable"  # glyphs the recogniser had no characters for


class Lane(str, Enum):
    """Who captured the evidence, which decides what it may be used for.

    This is a legal question, not a technical one, and it is independent of how
    good the photograph is. A citizen's phone snap can be pin-sharp and still
    not be evidence in an enforcement proceeding, because it was not taken under
    an inspector's authority and the pack was never seized. A marketplace
    listing image is worse still: it may not even depict the package the seller
    ships. Both are worth acting on -- they direct an inspector to a shelf --
    but neither may sustain a finding of non-conformity on its own.
    """

    FIELD = "field"
    MARKETPLACE = "marketplace"
    PREMARKET = "premarket"
    CITIZEN = "citizen"

    @property
    def can_sustain_violation(self) -> bool:
        """May a non-conformity found in this lane be recorded as a violation?

        FIELD is an inspector's own capture. PREMARKET is the manufacturer's own
        artwork, submitted by them for clearance -- there is no evidentiary gap
        to worry about, because the subject supplied the file.
        """
        return self in (Lane.FIELD, Lane.PREMARKET)

    @property
    def advisory_only(self) -> bool:
        return not self.can_sustain_violation

    @property
    def why_advisory(self) -> str:
        return {
            Lane.CITIZEN: (
                "This is a citizen submission. It is a referral for an inspector "
                "to verify, not evidence in an enforcement proceeding."
            ),
            Lane.MARKETPLACE: (
                "This is a marketplace listing image, which need not depict the "
                "package actually supplied. It directs an inspection; it cannot "
                "substitute for one."
            ),
        }.get(self, "")


class CaptureCompleteness(str, Enum):
    """How much of the package the capture actually shows.

    Orthogonal to AssuranceTier. Tier answers "how well do we know a
    millimetre"; this answers "are we entitled to say a declaration is absent".
    A razor-sharp Tier A capture of one face still cannot prove that the face
    we never photographed carries no consumer-care number.
    """

    NONE = "none"  # nothing usable was captured
    SINGLE = "single"  # one face
    PARTIAL = "partial"  # several faces, but not the whole package
    COMPLETE = "complete"  # every declaration-bearing face is in evidence

    @property
    def rank(self) -> int:
        return {"none": 0, "single": 1, "partial": 2, "complete": 3}[self.value]


class ExtractionPath(str, Enum):
    CLASSICAL = "A"  # detector + recogniser
    VLM = "B"  # document VLM under constrained decoding
    ARBITRATED = "arbitrated"
    ARTWORK = "artwork"  # read from vector source
    MANUAL = "manual"  # entered or corrected by an officer
