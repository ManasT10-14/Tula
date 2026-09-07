"""Barcode forensics.

The barcode is a free evidence channel that presence-detection pipelines throw
away. Two cheap checks come out of it:

  * the GS1 check digit, which catches a mistyped or fabricated GTIN;
  * the company prefix, whose allocating organisation can be compared against
    the country-of-origin the pack claims. A pack declaring "Made in India"
    under a prefix allocated outside India is a real, citable inconsistency
    that costs one table lookup.

The prefix says who *allocated* the number, not where the goods were made, so
this is corroborative evidence that prompts an officer to look -- never a
violation on its own. The findings it emits carry no penalty reference.
"""

from __future__ import annotations

from dataclasses import dataclass

# GS1 prefix ranges -> allocating organisation. Abridged to the ranges that
# actually turn up on Indian shelves plus the major exporting economies.
GS1_PREFIXES: list[tuple[int, int, str]] = [
    (0, 19, "United States"), (30, 39, "United States"), (60, 139, "United States"),
    (300, 379, "France"), (380, 380, "Bulgaria"), (383, 383, "Slovenia"),
    (385, 385, "Croatia"), (387, 387, "Bosnia and Herzegovina"),
    (400, 440, "Germany"), (450, 459, "Japan"), (490, 499, "Japan"),
    (460, 469, "Russia"), (471, 471, "Taiwan"), (474, 474, "Estonia"),
    (475, 475, "Latvia"), (476, 476, "Azerbaijan"), (477, 477, "Lithuania"),
    (478, 478, "Uzbekistan"), (479, 479, "Sri Lanka"), (480, 480, "Philippines"),
    (481, 481, "Belarus"), (482, 482, "Ukraine"), (484, 484, "Moldova"),
    (485, 485, "Armenia"), (486, 486, "Georgia"), (487, 487, "Kazakhstan"),
    (489, 489, "Hong Kong"), (500, 509, "United Kingdom"), (520, 521, "Greece"),
    (528, 528, "Lebanon"), (529, 529, "Cyprus"), (530, 530, "Albania"),
    (531, 531, "North Macedonia"), (535, 535, "Malta"), (539, 539, "Ireland"),
    (540, 549, "Belgium and Luxembourg"), (560, 560, "Portugal"),
    (569, 569, "Iceland"), (570, 579, "Denmark"), (590, 590, "Poland"),
    (594, 594, "Romania"), (599, 599, "Hungary"), (600, 601, "South Africa"),
    (603, 603, "Ghana"), (608, 608, "Bahrain"), (609, 609, "Mauritius"),
    (611, 611, "Morocco"), (613, 613, "Algeria"), (615, 615, "Nigeria"),
    (616, 616, "Kenya"), (618, 618, "Cote d'Ivoire"), (619, 619, "Tunisia"),
    (620, 620, "Tanzania"), (621, 621, "Syria"), (622, 622, "Egypt"),
    (624, 624, "Libya"), (625, 625, "Jordan"), (626, 626, "Iran"),
    (627, 627, "Kuwait"), (628, 628, "Saudi Arabia"), (629, 629, "United Arab Emirates"),
    (640, 649, "Finland"), (690, 699, "China"), (700, 709, "Norway"),
    (729, 729, "Israel"), (730, 739, "Sweden"), (740, 745, "Central America"),
    (746, 746, "Dominican Republic"), (750, 750, "Mexico"), (754, 755, "Canada"),
    (759, 759, "Venezuela"), (760, 769, "Switzerland"), (770, 771, "Colombia"),
    (773, 773, "Uruguay"), (775, 775, "Peru"), (777, 777, "Bolivia"),
    (778, 779, "Argentina"), (780, 780, "Chile"), (784, 784, "Paraguay"),
    (786, 786, "Ecuador"), (789, 790, "Brazil"), (800, 839, "Italy"),
    (840, 849, "Spain"), (850, 850, "Cuba"), (858, 858, "Slovakia"),
    (859, 859, "Czech Republic"), (860, 860, "Serbia"), (865, 865, "Mongolia"),
    (867, 867, "North Korea"), (868, 869, "Turkey"), (870, 879, "Netherlands"),
    (880, 880, "South Korea"), (883, 883, "Myanmar"), (884, 884, "Cambodia"),
    (885, 885, "Thailand"), (888, 888, "Singapore"), (890, 890, "India"),
    (893, 893, "Vietnam"), (896, 896, "Pakistan"), (899, 899, "Indonesia"),
    (900, 919, "Austria"), (930, 939, "Australia"), (940, 949, "New Zealand"),
    (955, 955, "Malaysia"), (958, 958, "Macau"),
    (977, 977, "Serial publication (ISSN)"), (978, 979, "Book (ISBN)"),
    (980, 980, "Refund receipt"), (981, 984, "Coupon"), (990, 999, "Coupon"),
]


@dataclass
class GtinCheck:
    gtin: str
    length_valid: bool
    check_digit_valid: bool
    prefix: str | None = None
    allocated_to: str | None = None
    is_restricted: bool = False  # 02, 04, 20-29: in-store / variable measure
    # A longer, valid GTIN this number is one lost digit away from. Its presence
    # means the read was almost certainly clipped rather than the number wrong.
    truncation_of: str | None = None

    @property
    def valid(self) -> bool:
        return self.length_valid and self.check_digit_valid

    @property
    def looks_truncated(self) -> bool:
        return self.truncation_of is not None

    @property
    def summary(self) -> str:
        if not self.length_valid:
            return f"{self.gtin} is not a valid GTIN length (8, 12, 13 or 14 digits)."
        if not self.check_digit_valid:
            if self.truncation_of:
                return (
                    f"{self.gtin} fails the GS1 check digit, but adding one leading "
                    f"digit yields {self.truncation_of}, which validates. The barcode "
                    "was most likely clipped at the edge of the frame or obscured. "
                    "Re-capture the full barcode before drawing any conclusion from it."
                )
            return (
                f"{self.gtin} fails the GS1 check digit. Either it was misread, or "
                "the number printed on the pack is not a validly allocated GTIN. "
                "Re-capture the barcode to distinguish the two."
            )
        if self.is_restricted:
            return (
                f"{self.gtin} uses a restricted-circulation prefix, which is assigned "
                "in-store rather than by GS1 and carries no origin information."
            )
        return f"{self.gtin} is a valid GTIN allocated by GS1 {self.allocated_to}."


def check_digit(digits: str) -> int:
    """GS1 modulo-10 check digit over the payload (without the check digit)."""
    total = 0
    # weights alternate 3,1,... reading right to left from the check position
    for index, char in enumerate(reversed(digits)):
        weight = 3 if index % 2 == 0 else 1
        total += int(char) * weight
    return (10 - (total % 10)) % 10


STANDARD_LENGTHS = (8, 12, 13, 14)
# Lengths that appear on a consumer pack, and so the only plausible targets for
# restoring a digit lost at the edge of a frame.
RETAIL_LENGTHS = (8, 12, 13)


def find_truncation(digits: str) -> str | None:
    """Is this number one lost *leading* digit away from a valid GTIN?

    A 13-digit EAN photographed with its first digit outside the frame reads as
    12 digits -- which is itself a legal GTIN-12 length, so the length check
    passes, the check digit then fails, and the pack gets accused of carrying a
    fabricated barcode on the strength of a framing error. Restoring the lost
    digit is a ten-way test, and it either validates or it does not.

    Only the leading digit is worth testing. If the *trailing* digit were the
    one clipped, then what we read is the payload and there is always exactly
    one check digit that completes it -- the test would succeed on every input
    and so prove nothing at all.

    GTIN-14 is excluded as a restoration target. It is a logistics code for a
    case or pallet, not something printed on a consumer pack, so "this 13-digit
    EAN is really a 14 with a digit missing" is not a story about a photograph.
    Allowing it would also make the test nearly useless: with nine leading
    digits to try, a spurious match is likelier than not.
    """
    for length in RETAIL_LENGTHS:
        if length != len(digits) + 1:
            continue
        for lead in "123456789":  # a leading zero cannot change the check digit
            candidate = lead + digits
            if check_digit(candidate[:-1]) == int(candidate[-1]):
                return candidate
    return None


def allocating_org(gtin: str) -> tuple[str | None, str | None, bool]:
    """Resolve the GS1 prefix of a GTIN to its allocating organisation."""
    padded = gtin.zfill(13)
    prefix = padded[:3]
    value = int(prefix)
    # Restricted circulation: 020-029, 040-049 and 200-299 are assigned inside a
    # company or region rather than by GS1, so they carry no origin information
    # at all and must never feed an origin comparison.
    if 20 <= value <= 29 or 40 <= value <= 49 or 200 <= value <= 299:
        return prefix, None, True
    for low, high, org in GS1_PREFIXES:
        if low <= value <= high:
            return prefix, org, False
    return prefix, None, False


def check(gtin: str) -> GtinCheck:
    digits = "".join(c for c in gtin if c.isdigit())
    if len(digits) not in STANDARD_LENGTHS:
        return GtinCheck(
            gtin=digits or gtin,
            length_valid=False,
            check_digit_valid=False,
            truncation_of=find_truncation(digits) if digits else None,
        )

    body, given = digits[:-1], int(digits[-1])
    valid = check_digit(body) == given
    prefix, org, restricted = allocating_org(digits)
    return GtinCheck(
        gtin=digits,
        length_valid=True,
        check_digit_valid=valid,
        prefix=prefix,
        allocated_to=org,
        is_restricted=restricted,
        # only meaningful when the number failed: a valid GTIN is not a truncation
        truncation_of=None if valid else find_truncation(digits),
    )


def origin_conflict(gtin_result: GtinCheck, declared_country: str | None) -> str | None:
    """Compare the allocating organisation with the declared country of origin.

    Returns a sentence describing the inconsistency, or None. Phrased carefully:
    a GS1 prefix records who allocated the number, and a company may legitimately
    use a prefix from one country for goods made in another.
    """
    if not gtin_result.valid or gtin_result.is_restricted:
        return None
    if not declared_country or not gtin_result.allocated_to:
        return None

    declared = declared_country.strip().lower()
    allocated = gtin_result.allocated_to.strip().lower()
    if declared in allocated or allocated in declared:
        return None

    return (
        f"The package declares its origin as {declared_country}, but its GTIN "
        f"{gtin_result.gtin} carries prefix {gtin_result.prefix}, allocated by GS1 "
        f"{gtin_result.allocated_to}. A prefix records the allocating member "
        "organisation rather than the place of manufacture, so this is a prompt "
        "to verify the origin declaration, not a violation in itself."
    )


def best_candidate(candidates: list[str]) -> GtinCheck | None:
    """Pick the most credible GTIN from OCR noise.

    Order of preference: one that validates outright; then one that validates
    once a clipped leading digit is restored; then whatever is longest, since a
    near-complete read is more informative than a short fragment of one.
    """
    checks = [check(c) for c in candidates]
    if not checks:
        return None
    valid = [c for c in checks if c.valid]
    if valid:
        return max(valid, key=lambda c: len(c.gtin))
    recoverable = [c for c in checks if c.looks_truncated]
    if recoverable:
        return max(recoverable, key=lambda c: len(c.gtin))
    return max(checks, key=lambda c: len(c.gtin))
