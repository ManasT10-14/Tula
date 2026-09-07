"""The expression interpreter.

Small on purpose: about twenty operators, three-valued logic, no eval(), no
imports from the rest of the pipeline. A rule pack is untrusted-ish input
(a legal officer edits it), so the interpreter must not be able to do anything
except answer a question about the facts it was handed.

Three-valued (Kleene) logic is the load-bearing design choice. `None` means
"the evidence does not decide this", which is exactly what happens when a
measurement interval straddles a statutory limit. It propagates correctly
through AND/OR, so an INCONCLUSIVE measurement anywhere in a conjunction makes
the whole rule inconclusive rather than silently passing or silently accusing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

from ..domain.enums import AssuranceTier, Script
from ..domain.models import Measured

Tri = bool | None  # True | False | None(=unknown)


# --------------------------------------------------------------------------
# Kleene connectives
# --------------------------------------------------------------------------


def k_and(values: list[Tri]) -> Tri:
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def k_or(values: list[Tri]) -> Tri:
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def k_not(value: Tri) -> Tri:
    return None if value is None else (not value)


# --------------------------------------------------------------------------
# Evaluation context
# --------------------------------------------------------------------------


@dataclass
class EvalContext:
    """Facts a rule may reason about, plus a trace of what it looked at.

    The trace is why the report can say "measured 1.42 mm against a 2.00 mm
    threshold derived from a 214 cm2 panel" instead of just "failed".

    `coverage` is what makes `present` honest. Without it the operator has no
    way to distinguish a declaration that is missing from the package from one
    that is missing from the photograph, and it would have to guess -- which in
    practice means guessing "violation" against a compliant manufacturer.
    `absence_provable_from` is supplied per rule and names the panels the
    declaration must lawfully appear on.
    """

    facts: dict[str, Any]
    trace: dict[str, Any] = field(default_factory=dict)
    coverage: Any | None = None  # domain.models.EvidenceCoverage
    absence_provable_from: list[Any] = field(default_factory=list)

    def resolve(self, path: str) -> Any:
        """Resolve a `$a.b.c` path. Missing anywhere in the chain -> None."""
        if not isinstance(path, str) or not path.startswith("$"):
            return path
        node: Any = self.facts
        for part in path[1:].split("."):
            if isinstance(node, dict):
                node = node.get(part)
            else:
                node = getattr(node, part, None)
            if node is None:
                return None
        return node

    def absence_is_provable(self) -> bool:
        """Is this capture entitled to say a declaration is not on the package?

        Absent a coverage model the answer is yes, so that unit tests and any
        caller assembling facts by hand keep meaning exactly what they say. The
        real pipeline always supplies one.
        """
        if self.coverage is None:
            return True
        ok, reason = self.coverage.can_prove_absence(self.absence_provable_from)
        if not ok:
            self.trace["absence_unprovable"] = reason
        return ok


class RuleEvalError(ValueError):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    """Unwrap a value to something comparable.

    `str`-mixin enums are the trap here: `PackageClass.RETAIL` passes
    `isinstance(x, str)` but stringifies to "PackageClass.RETAIL", so a naive
    `str(left) == str(right)` silently never matches a rule pack's "retail".
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Measured):
        return value.value
    return value


def _numeric(value: Any) -> float | None:
    if isinstance(value, Measured):
        return value.value
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _interval(value: Any) -> tuple[float, float] | None:
    """Lower/upper bounds of a value, treating a plain number as exact."""
    if isinstance(value, Measured):
        return value.lower, value.upper
    num = _numeric(value)
    return None if num is None else (num, num)


def _norm_text(text: str) -> str:
    text = text.lower()
    text = text.replace("₹", " rs ").replace("rs.", " rs ")
    text = re.sub(r"[^a-z0-9ऀ-ॿ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fuzzy_contains(haystack: str, needle: str, threshold: float = 0.85) -> bool:
    """Is `needle` present in `haystack`, allowing for OCR damage?

    Rule 6(1)(e) prescribes an exact phrase, but the camera does not deliver
    exact phrases. A sliding window over normalised text with a similarity
    floor is the honest middle ground: strict enough that a missing tax clause
    is caught, loose enough that one misread character is not a violation.
    """

    hay, need = _norm_text(haystack), _norm_text(needle)
    if not need:
        return True
    if need in hay:
        return True
    hw, nw = hay.split(), need.split()
    if len(hw) < len(nw):
        return SequenceMatcher(None, hay, need).ratio() >= threshold
    best = 0.0
    for i in range(len(hw) - len(nw) + 1):
        window = " ".join(hw[i : i + len(nw)])
        best = max(best, SequenceMatcher(None, window, need).ratio())
        if best >= threshold:
            return True
    return best >= threshold


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------


def _op_table_lookup(ctx: EvalContext, arg: dict[str, Any]) -> float | None:
    """Pick a threshold from a banded table (Rule 8's tables are exactly this).

    `boundary_policy: favour_subject` matters more than it looks. A panel
    measured at 495 +/- 12 cm2 straddles the 500 cm2 boundary where the
    required height doubles from 2 mm to 4 mm. Charging the higher threshold on
    an uncertain area would manufacture violations, so the lower band wins and
    the choice is recorded in the trace.
    """

    key_raw = ctx.resolve(arg["key"])
    if key_raw is None:
        return None
    interval = _interval(key_raw)
    if interval is None:
        return None
    lo, hi = interval

    policy = arg.get("boundary_policy", "point")
    select = arg.get("select")
    column = "value"
    if isinstance(select, dict):
        cond = ctx.resolve(select.get("if"))
        column = select.get("then") if cond else select.get("else")
    elif isinstance(select, str):
        column = select

    def band_for(x: float) -> Any:
        for row in arg["rows"]:
            cap = row.get("max")
            if cap is None or x <= cap:
                return row
        return arg["rows"][-1]

    if policy == "favour_subject":
        candidates = [band_for(lo), band_for(hi)]
    else:
        candidates = [band_for((lo + hi) / 2.0)]

    values: list[float] = []
    for row in candidates:
        cell = row.get(column, row.get("value"))
        if isinstance(cell, dict):
            cell = cell.get(column)
        if cell is not None:
            values.append(float(cell))
    if not values:
        return None

    chosen = min(values) if policy == "favour_subject" else values[0]
    ctx.trace["threshold_basis"] = (
        f"{arg.get('key', '')[1:]} = "
        + (key_raw.render() if isinstance(key_raw, Measured) else f"{lo:g}")
        + f" -> band column '{column}'"
        + (", boundary resolved in favour of the subject" if len(set(values)) > 1 else "")
    )
    return chosen


def _op_gte_measured(ctx: EvalContext, args: list[Any]) -> Tri:
    """Guard-banded conformity against a lower limit.

    This is the decision rule from ILAC-G8, adapted: conformity is only decided
    where the measurement interval sits wholly on one side of the limit.
    Anything else is INCONCLUSIVE, which is the system declining to accuse
    someone on evidence that does not support the accusation.
    """

    measure = ctx.resolve(args[0])
    threshold = evaluate_value(ctx, args[1])
    if measure is None or threshold is None:
        return None

    interval = _interval(measure)
    if interval is None:
        return None
    lo, hi = interval
    limit = float(threshold)

    if isinstance(measure, Measured):
        ctx.trace["measured"] = measure
    ctx.trace["threshold"] = limit

    if lo > limit or (lo == hi and lo >= limit):
        return True  # conformity proven
    if hi < limit:
        return False  # non-conformity proven beyond the uncertainty
    return None  # straddles the limit -> re-capture at a higher tier


def _op_script_coverage(ctx: EvalContext, args: list[Any]) -> Tri:
    """Rule 9(3): the declaration must appear in Hindi *and* English.

    Widely ignored in the market and essentially never audited, because it
    needs script identification rather than translation.
    """

    decl = ctx.resolve(f"$decl.{args[0]}")
    if decl is None:
        return None
    required = {Script(s) if not isinstance(s, Script) else s for s in args[1]}
    have = set(getattr(decl, "scripts", []) or [])
    missing = required - have
    ctx.trace["scripts_present"] = sorted(s.value for s in have)
    ctx.trace["scripts_missing"] = sorted(s.value for s in missing)

    if missing and Script.UNREADABLE in have:
        # Text was present but the recogniser could not transcribe it. That is a
        # gap in our evidence, not a gap on the label.
        ctx.trace["scripts_note"] = (
            "Writing was detected that the recogniser could not transcribe, so "
            "the absence of "
            + ", ".join(sorted(s.value for s in missing))
            + " cannot be established. A script-capable recogniser is required "
            "to decide this."
        )
        return None
    return not missing


def _op_present(ctx: EvalContext, path: Any) -> Tri:
    """Three-valued presence: found / not on the package / cannot tell.

    The middle answer is the one that matters. "I did not find a retail sale
    price" is only a violation if we were in a position to find one -- the pack
    fully in frame, and legible enough that we would have read the price had it
    been there. Otherwise the honest answer is that the evidence does not
    decide, and the officer is told to re-capture rather than the manufacturer
    told they broke the law.

    A second, narrower case sits in between: the declaration was located but an
    attribute of it could not be derived, e.g. a line cued "Net Wt." carrying no
    parseable quantity. That is a gap in the reading, not a proven gap on the
    label, so it is undecided too -- and traced separately, because the remedy
    is different (verify the printed text, not re-photograph the pack).
    """

    if ctx.resolve(path) is not None:
        return True

    if isinstance(path, str) and path.startswith("$"):
        parts = path[1:].split(".")
        # `$decl.<class>` is the declaration; anything deeper is an attribute of
        # one. If the declaration itself resolved, the gap is in normalisation.
        if len(parts) > 2 and ctx.resolve("$" + ".".join(parts[:2])) is not None:
            ctx.trace["parse_gap"] = (
                f"The declaration was located, but '{parts[-1]}' could not be "
                "derived from what was printed. Verify the declaration by eye."
            )
            return None

    return False if ctx.absence_is_provable() else None


def _op_contains_phrase(ctx: EvalContext, args: list[Any]) -> Tri:
    subject = ctx.resolve(args[0])
    if subject is None:
        return None
    phrases = args[1] if isinstance(args[1], list) else [args[1]]
    threshold = float(args[2]) if len(args) > 2 else 0.85
    return any(fuzzy_contains(str(subject), p, threshold) for p in phrases)


def _op_tier_at_least(ctx: EvalContext, arg: Any) -> Tri:
    tier = ctx.resolve("$ctx.tier")
    if tier is None:
        return None
    required = AssuranceTier(arg)
    return AssuranceTier(tier).satisfies(required)


def _cmp(ctx: EvalContext, args: list[Any], fn) -> Tri:
    left = _numeric(evaluate_value(ctx, args[0]))
    right = _numeric(evaluate_value(ctx, args[1]))
    if left is None or right is None:
        return None
    return fn(left, right)


def _op_approx_eq(ctx: EvalContext, args: list[Any]) -> Tri:
    """Equality with a relative tolerance -- the unit-price arithmetic audit.

    The 2022 amendment made unit sale price mandatory; nobody checks whether
    the printed number is arithmetically correct. This is that check.
    """

    left = _numeric(evaluate_value(ctx, args[0]))
    right = _numeric(evaluate_value(ctx, args[1]))
    if left is None or right is None:
        return None
    tol = float(args[2]) if len(args) > 2 else 0.02
    if right == 0:
        return abs(left) <= tol
    delta = abs(left - right) / abs(right)
    ctx.trace["arithmetic"] = (
        f"declared {left:g} vs computed {right:g} (deviation {delta * 100:.1f}%)"
    )
    return delta <= tol


def _op_divide(ctx: EvalContext, args: list[Any]) -> float | None:
    num = _numeric(evaluate_value(ctx, args[0]))
    den = _numeric(evaluate_value(ctx, args[1]))
    if num is None or den in (None, 0):
        return None
    # the scale factor is an expression too -- for the unit-price audit it is
    # "$decl.unit_sale_price.norm.per_base", not a literal
    scale = _numeric(evaluate_value(ctx, args[2])) if len(args) > 2 else 1.0
    if scale is None:
        return None
    return num / den * scale


def _op_round_step(ctx: EvalContext, args: list[Any]) -> Tri:
    """Rule 11: MRP must be rounded to the nearest rupee or 50 paise."""
    value = _numeric(evaluate_value(ctx, args[0]))
    if value is None:
        return None
    step = float(args[1]) if len(args) > 1 else 0.5
    remainder = round((value / step) - round(value / step), 6)
    return abs(remainder) < 1e-6


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def evaluate_value(ctx: EvalContext, node: Any) -> Any:
    """Evaluate an expression that yields a value rather than a truth."""
    if isinstance(node, str) and node.startswith("$"):
        return ctx.resolve(node)
    if isinstance(node, dict) and len(node) == 1:
        op, arg = next(iter(node.items()))
        if op == "table_lookup":
            return _op_table_lookup(ctx, arg)
        if op == "divide":
            return _op_divide(ctx, arg)
        if op == "value_of":
            resolved = ctx.resolve(arg)
            return _numeric(resolved) if resolved is not None else None
        if op == "len":
            resolved = ctx.resolve(arg)
            return None if resolved is None else len(resolved)
        return evaluate(ctx, node)
    return node


def evaluate(ctx: EvalContext, node: Any) -> Tri:
    """Evaluate an expression that yields a truth value."""

    if node is True or node is False:
        return node
    if node is None:
        return None
    if isinstance(node, str):
        resolved = ctx.resolve(node)
        return None if resolved is None else bool(resolved)
    if not isinstance(node, dict):
        return bool(node)
    if len(node) != 1:
        raise RuleEvalError(f"expression must have exactly one operator: {node!r}")

    op, arg = next(iter(node.items()))

    if op == "all":
        return k_and([evaluate(ctx, a) for a in arg])
    if op == "any":
        values = []
        traces = []
        for a in arg:
            branch = EvalContext(ctx.facts, {}, ctx.coverage, ctx.absence_provable_from)
            result = evaluate(branch, a)
            if result is True:
                ctx.trace.update(branch.trace)
                return True
            values.append(result)
            traces.append(branch.trace)
        for trace in traces:
            ctx.trace.update(trace)
        return k_or(values)
    if op == "not":
        return k_not(evaluate(ctx, arg))

    if op == "present":
        return _op_present(ctx, arg)
    if op == "absent":
        return k_not(_op_present(ctx, arg))
    if op == "truthy":
        resolved = ctx.resolve(arg)
        return None if resolved is None else bool(resolved)

    if op == "eq":
        left, right = evaluate_value(ctx, arg[0]), evaluate_value(ctx, arg[1])
        if left is None or right is None:
            return None
        return str(_scalar(left)).strip().lower() == str(_scalar(right)).strip().lower()
    if op == "ne":
        result = evaluate(ctx, {"eq": arg})
        return k_not(result)
    if op == "gt":
        return _cmp(ctx, arg, lambda a, b: a > b)
    if op == "gte":
        return _cmp(ctx, arg, lambda a, b: a >= b)
    if op == "lt":
        return _cmp(ctx, arg, lambda a, b: a < b)
    if op == "lte":
        return _cmp(ctx, arg, lambda a, b: a <= b)
    if op == "in":
        left = evaluate_value(ctx, arg[0])
        return None if left is None else left in arg[1]

    if op == "matches":
        subject = ctx.resolve(arg[0])
        if subject is None:
            return None
        return re.search(arg[1], str(subject), re.IGNORECASE | re.UNICODE) is not None
    if op == "contains_phrase":
        return _op_contains_phrase(ctx, arg)

    if op == "gte_measured":
        return _op_gte_measured(ctx, arg)
    if op == "approx_eq":
        return _op_approx_eq(ctx, arg)
    if op == "same_units_arithmetic":
        left, right = ctx.resolve(arg[0]), ctx.resolve(arg[1])
        if left is None or right is None or left != right:
            ctx.trace["arithmetic"] = "Unit-price dimensions differ or are unreadable; recapture and review the units"
            return None
        return evaluate(ctx, arg[2])
    if op == "unit_price_matches":
        try:
            declared, price, quantity, basis = [Decimal(str(ctx.resolve(a))) for a in arg]
            if not all(v.is_finite() for v in (declared, price, quantity, basis)) or quantity <= 0 or basis <= 0:
                return None
            expected = (price / quantity * basis).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
        ctx.trace['arithmetic'] = f"Declared {declared} vs computed {expected}, rounded to two decimal places"
        return declared == expected
    if op == "rounded_to":
        return _op_round_step(ctx, arg)
    if op == "script_coverage":
        return _op_script_coverage(ctx, arg)
    if op == "tier_at_least":
        return _op_tier_at_least(ctx, arg)
    if op == "legal_screen":
        from .legal import assertion
        return assertion(ctx, arg)

    if op == "exempt_under":
        exemptions = ctx.resolve("$pkg.exemptions") or []
        wanted = arg if isinstance(arg, list) else [arg]
        return any(any(e.startswith(w) for e in exemptions) for w in wanted)

    if op == "panel_is":
        decl = ctx.resolve(f"$decl.{arg[0]}")
        if decl is None:
            return None
        panel = getattr(decl, "panel", None)
        if panel is None or panel.value == "unknown":
            return None  # not captured -> undecidable, not compliant
        wanted = arg[1] if isinstance(arg[1], list) else [arg[1]]
        return panel.value in wanted

    if op == "same_panel":
        panels = set()
        missing = False
        for name in arg:
            decl = ctx.resolve(f"$decl.{name}")
            if decl is None:
                missing = True
                continue
            panel = getattr(decl, "panel", None)
            if panel is None or panel.value == "unknown":
                return None
            panels.add(panel.value)
        if len(panels) > 1:
            return False
        return None if missing or not panels else True

    raise RuleEvalError(f"unknown operator {op!r}")
