"""Deciding a height rule when nobody measured the package.

Rule 8 is stated in millimetres, and a photograph has no millimetres in it.
The usual consequence is an inconclusive verdict and a second visit with the
scale card. That is right when the answer really does depend on the scale --
and it is unnecessary when it does not.

Write the rule out. Let ``u`` be the geometric mean side of the panel in
millimetres, so the panel area is ``u^2 / 100`` square centimetres, and let
``r`` be the ratio of the measured glyph height to the panel's pixel extent,
which a photograph *does* determine. Then the printed height is ``r * u`` and
Rule 8 asks::

    r * u  >=  T(u^2 / 100)

``T`` is the Table I step function. One unknown remains: ``u``. Sweep it
across every package size the declared quantity allows and two useful things
can happen. If the inequality fails everywhere, the package is short whatever
its true size is. If it holds everywhere, the package complies whatever its
true size is, and nobody needs to walk back to the shop with a scale card.

Which end of the panel bracket is used depends on which way the answer cuts,
and the choice is always the one that favours the package:

* to say a package is *short*, the ratio is taken against the printed text
  hull -- the smallest the panel can be -- because a small panel makes the
  ratio large and a shortfall harder to prove;
* to say a package *complies*, the ratio is taken against the outer bound,
  because a large panel makes the ratio small and compliance harder to prove.

Neither conclusion is a millimetre measurement. The sweep bound comes from the
declared quantity and a density prior, which is Tier C evidence, so the
shortfall is reported as an advisory that sends an inspector back with the
card. The tier gate in ``engine.py`` would refuse to make it anything more.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain.enums import AssuranceTier, Severity, Verdict
from ..domain.models import Citation, Finding, Measured, PackageFacts, Scan, short_hash
from .evaluator import EvalContext, evaluate
from .spec import Rule, RulePack

# Bounds on the geometric mean side of a retail package, in millimetres, used
# when the declared quantity gives us nothing to narrow them with. A 15 mm
# floor is a sachet; a 600 mm ceiling is a carton nobody carries one-handed.
DEFAULT_SPAN_MM = (15.0, 600.0)

# Bulk density used to place the package's likely size, and the densest a
# packaged food is assumed to be when deriving the size *floor*. Salt and
# honey exceed 1.0 g/cm3, so the floor is a screening bound, not a theorem.
PRIOR_DENSITY = {"g": 0.55, "ml": 1.0}
FLOOR_DENSITY = 1.0

# How much larger than its contents a package may be. Headspace, gas flush and
# crisp bags all push one way, and eight times the product volume is generous;
# in linear terms that is a factor of two on every side.
HEADSPACE_LINEAR = 2.0

SWEEP_POINTS = 61


@dataclass
class HeightRatio:
    """A glyph height and the panel it sits on, both measured in pixels.

    The panel is a bracket rather than a number, so the ratio is a bracket
    too, and the two ends are used for opposite conclusions.
    """

    quantity: str
    cap_height_px: float
    panel_min_px: float
    panel_max_px: float
    sigma_px: float = 0.0

    @property
    def ratio_high(self) -> float:
        """Largest ratio the evidence allows: the smallest possible panel."""
        return self.cap_height_px / self.panel_min_px if self.panel_min_px else 0.0

    @property
    def ratio_low(self) -> float:
        """Smallest ratio the evidence allows: the largest possible panel."""
        return self.cap_height_px / self.panel_max_px if self.panel_max_px else 0.0

    @property
    def rel_sigma(self) -> float:
        return (self.sigma_px / self.cap_height_px) if self.cap_height_px else 0.0


def span_bounds_mm(
    net_quantity_base: float | None, unit_base: str | None
) -> tuple[float, float]:
    """Bracket the panel's physical size from the declared quantity.

    The floor is geometric rather than assumed. For a box with sides a >= b >= c
    holding volume V, the largest face satisfies ``ab >= V^(2/3)``, so its
    geometric mean side is at least the cube root of the volume -- a flat pouch
    has a *bigger* face than a cube of the same contents, never a smaller one.
    The ceiling is the density prior's estimate with headspace allowed for.
    """
    density = PRIOR_DENSITY.get(unit_base or "")
    if not net_quantity_base or density is None:
        return DEFAULT_SPAN_MM
    # Millimetre side of a cube holding the contents at their densest.
    floor = ((net_quantity_base / FLOOR_DENSITY) ** (1 / 3)) * 10.0
    ceiling = ((net_quantity_base / density) ** (1 / 3)) * 10.0 * HEADSPACE_LINEAR
    low = max(DEFAULT_SPAN_MM[0], floor)
    high = min(DEFAULT_SPAN_MM[1], ceiling)
    return (low, high) if low < high else DEFAULT_SPAN_MM


def _intersect(prior: tuple[float, float],
               measured: tuple[float, float] | None) -> tuple[float, float]:
    """Overlap of two size brackets, or the prior when they do not overlap.

    Disagreement is not resolved here by preferring one source. Two brackets
    that do not overlap mean at least one of them is wrong about this package,
    and narrowing to an empty or arbitrary range would manufacture a confident
    answer out of a contradiction. Falling back to the prior keeps the sweep
    wide, which can only make the screen abstain more often.
    """
    if measured is None:
        return prior
    low = max(prior[0], measured[0])
    high = min(prior[1], measured[1])
    return (low, high) if low < high else prior


def _measurement_keys(node) -> set[str]:
    """Every ``$meas.x`` a rule assertion reads."""
    if isinstance(node, str):
        return {node[len("$meas."):]} if node.startswith("$meas.") else set()
    if isinstance(node, dict):
        return set().union(*(_measurement_keys(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_measurement_keys(v) for v in node)) if node else set()
    return set()


def _reads_panel_area(node) -> bool:
    if isinstance(node, str):
        return node == "$pkg.pdp_area_cm2"
    if isinstance(node, dict):
        return any(_reads_panel_area(v) for v in node.values())
    if isinstance(node, list):
        return any(_reads_panel_area(v) for v in node)
    return False


def scale_dependent_rules(pack: RulePack) -> list[Rule]:
    """Rules whose only missing input is the millimetre scale."""
    return [
        rule for rule in pack.rules
        if _reads_panel_area(rule.assertion) and _measurement_keys(rule.assertion)
    ]


def _sweep(
    rule: Rule,
    ratio: float,
    rel_sigma: float,
    scan: Scan,
    package: PackageFacts,
    declarations: dict,
    bounds: tuple[float, float],
    build_facts,
    *,
    want: bool,
) -> tuple[bool, float, float]:
    """Evaluate one rule at every package size in the bracket.

    ``want`` is the assertion outcome being tested for: ``False`` asks whether
    the rule fails everywhere, ``True`` whether it holds everywhere. Returns
    that answer and the size at which the margin was tightest, which is what
    an officer needs to see before believing any of it.
    """
    low, high = bounds
    step = (math.log(high) - math.log(low)) / (SWEEP_POINTS - 1)
    everywhere = True
    tightest = math.inf
    at_span = low
    for index in range(SWEEP_POINTS):
        span_mm = math.exp(math.log(low) + index * step)
        height_mm = ratio * span_mm
        trial = package.model_copy(update={
            "pdp_area_cm2": Measured(
                quantity="pdp_area", value=(span_mm * span_mm) / 100.0, uncertainty=0.0,
                unit="cm2", tier=AssuranceTier.C, sources=["scale-free sweep"],
                method=f"panel of {span_mm:.0f} mm mean side",
            )
        })
        measurements = {
            key: Measured(
                quantity=key, value=height_mm,
                uncertainty=2 * height_mm * rel_sigma,
                unit="mm", tier=AssuranceTier.C, sources=["scale-free sweep"],
                method=f"{ratio * 100:.2f}% of a {span_mm:.0f} mm panel",
            )
            for key in _measurement_keys(rule.assertion)
        }
        facts = build_facts(scan, trial, declarations, measurements)
        ctx = EvalContext(
            facts=facts,
            coverage=scan.coverage,
            absence_provable_from=list(rule.absence_provable_from),
        )
        if evaluate(ctx, rule.assertion) is not want:
            everywhere = False
            break
        threshold = ctx.trace.get("threshold")
        if threshold is not None:
            margin = abs(float(threshold) - height_mm)
            if margin < tightest:
                tightest, at_span = margin, span_mm
    return everywhere, tightest, at_span


_SHORT_CITATION = Citation(
    clause="Scale-invariant screening of Rule 7(2), Table I",
    text=(
        "Not a separate legal duty. The Table I minimum was tested across every "
        "package size the declared quantity permits; where it fails throughout "
        "that range, the shortfall does not depend on knowing the millimetre "
        "scale of the photograph."
    ),
)

_CLEAR_CITATION = Citation(
    clause="Scale-invariant screening of Rule 7(2), Table I",
    text=(
        "Not a legal clearance. The Table I minimum was tested across every "
        "package size the declared quantity permits and was met throughout, so "
        "a scale-referenced re-capture would not change this rule's outcome."
    ),
)


def screen(
    pack: RulePack,
    scan: Scan,
    package: PackageFacts,
    declarations: dict,
    ratios: list[HeightRatio],
    build_facts,
    *,
    rules_version: str,
    measured_bounds: tuple[float, float] | None = None,
) -> list[Finding]:
    """Screen the height rules against every package size the evidence allows.

    Called only for rules the ordinary evaluation could not decide. A rule that
    already reached a verdict on a real millimetre scale does not want a weaker
    second opinion printed beside it.

    `measured_bounds` is an independent bracket on the panel's physical size,
    derived from something actually in the photograph rather than from a density
    prior -- in practice the package's own barcode, whose module width the GS1
    magnification range bounds. Where both are available the sweep runs over
    their intersection, because the true size has to satisfy both, and a
    narrower sweep is decisive on packages a wider one has to abstain on.
    """
    if not ratios:
        return []
    bounds = span_bounds_mm(package.capacity_value, package.capacity_unit)
    bounds = _intersect(bounds, measured_bounds)
    findings: list[Finding] = []
    for rule in scale_dependent_rules(pack):
        keys = _measurement_keys(rule.assertion)
        for ratio in ratios:
            if ratio.quantity not in keys:
                continue
            finding = _screen_one(
                rule, ratio, scan, package, declarations, bounds, build_facts,
                rules_version=rules_version,
            )
            if finding is not None:
                findings.append(finding)
            break
    return findings


def _screen_one(
    rule: Rule,
    ratio: HeightRatio,
    scan: Scan,
    package: PackageFacts,
    declarations: dict,
    bounds: tuple[float, float],
    build_facts,
    *,
    rules_version: str,
) -> Finding | None:
    short, gap, span = _sweep(
        rule, ratio.ratio_high, ratio.rel_sigma, scan, package, declarations,
        bounds, build_facts, want=False,
    )
    bracket = (
        f"Panel sizes from {bounds[0]:.0f} mm to {bounds[1]:.0f} mm mean side were "
        "tested, which is the range the declared quantity allows."
        if package.capacity_value else
        f"No quantity was read, so every panel from {bounds[0]:.0f} mm to "
        f"{bounds[1]:.0f} mm mean side was tested."
    )
    if short:
        return Finding(
            finding_id=f"F-{scan.scan_id}-{short_hash('scalefree.short.' + rule.id, 6)}",
            rule_id=f"SCREEN.SCALE_FREE.{rule.id}",
            rules_version=rules_version,
            citation=_SHORT_CITATION,
            declaration=rule.declaration,
            # Tier C by construction: the sweep rests on a density prior.
            # Advisory is the strongest standing this may hold.
            verdict=Verdict.ADVISORY,
            severity=Severity.MINOR,
            message="Declaration height falls short at every package size this quantity allows",
            detail=(
                f"The printed characters measure {ratio.ratio_high * 100:.2f}% of the panel's "
                f"own extent. {bracket} The height required by Table I is never met; the "
                f"margin is tightest at {span:.0f} mm, where the declaration is still "
                f"{gap:.2f} mm short. The ratio is taken against the printed text hull, the "
                "smallest the panel can be, so a larger panel only widens the shortfall. "
                "Re-capture with the scale card in frame to turn this into a measurement."
            ),
            evidence=["scale_free_ratio", "declared_quantity_bound"],
            tier=AssuranceTier.C,
        )

    met, margin, span = _sweep(
        rule, ratio.ratio_low, ratio.rel_sigma, scan, package, declarations,
        bounds, build_facts, want=True,
    )
    if met:
        return Finding(
            finding_id=f"F-{scan.scan_id}-{short_hash('scalefree.clear.' + rule.id, 6)}",
            rule_id=f"SCREEN.SCALE_FREE.{rule.id}",
            rules_version=rules_version,
            citation=_CLEAR_CITATION,
            declaration=rule.declaration,
            # The screening ratio passed, which is all this claims. The
            # statutory finding beside it stays inconclusive, and the citation
            # says in terms that this is not a clearance under the Rules.
            verdict=Verdict.PASS,
            severity=Severity.MINOR,
            message="Height requirement is met at every package size this quantity allows",
            detail=(
                f"The printed characters measure at least {ratio.ratio_low * 100:.2f}% of the "
                f"panel's own extent. {bracket} The height required by Table I is met "
                f"throughout; the margin is tightest at {span:.0f} mm, with {margin:.2f} mm "
                "to spare. The ratio is taken against the outer bound of the panel, the "
                "largest it can be, so a smaller panel only widens the margin. Returning "
                "with a scale card would not change this rule's outcome."
            ),
            evidence=["scale_free_ratio", "declared_quantity_bound"],
            tier=AssuranceTier.C,
        )
    return None
