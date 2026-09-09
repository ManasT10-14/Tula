"""Which confirmable facts are holding checks up, and how many each would free.

An inspector standing in a shop does not need to be told that a check is
"inconclusive pending category-aware screening". They need to be told: *tick
the product category and four more checks decide themselves*. The console has
always had a form for these facts, and no way to say what any of them was for.

The mapping is derived rather than written down. Writing it down would mean
listing, beside each rule, the facts its gates happen to consult -- a second
copy of the applicability logic, in a second place, going stale on its own
schedule. Instead each candidate fact is set to each value it can take, the
pack is re-evaluated, and the rules that stop being undecided are counted. What
the officer is shown is then true by construction, including for rules added to
the pack later, and it costs one rules pass per value on an engine that runs the
whole pack in tens of milliseconds.

The count is an upper bound and is worded as one: confirming a category as
"medical device" refers the height rules to another set of rules altogether
rather than deciding them, so a fact is credited with a rule when *some* value
of it would decide that rule, never on the assumption that the officer's answer
will be the convenient one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import Verdict

# Each confirmable fact, the legal_context keys a confirmation writes, and the
# values it can take. These mirror the package-facts form in the console; the
# form is the thing the officer actually fills in, so its options define the
# question rather than the other way round.
FACTS: dict[str, dict] = {
    "category": {
        "label": "the product category",
        "action": "Tick what kind of commodity this is",
        "field": "category",
        # `confirmed_category` is what a rule compares against, and it is
        # normally derived in `exemptions.apply`. A hypothetical here skips that
        # step, so it has to be set alongside -- otherwise a rule gated on the
        # settled category would never appear releasable and the officer would
        # never be told that confirming it decides anything.
        "values": [{"category": value, "category_confirmed": True,
                    "confirmed_category": value}
                   for value in ("general", "food", "alcohol", "tobacco", "pan_masala",
                                 "medical_device", "cosmetic", "seed")],
        "confirmed": lambda legal: legal.get("category_confirmed") is True,
    },
    "bundle_type": {
        "label": "whether this is a single or multi-piece package",
        "action": "Tick whether this is a single pack or a combination, group or multi-piece package",
        "field": "bundle_type",
        "values": [{"bundle_type": value, "bundle_confirmed": True}
                   for value in ("single", "combination", "group", "multipiece")],
        "confirmed": lambda legal: legal.get("bundle_confirmed") is True,
    },
    "origin": {
        "label": "whether the commodity was imported",
        "action": "Tick whether this commodity was imported or made in India",
        "field": "origin",
        "values": [{"is_imported": False, "imported_confirmed": True},
                   {"is_imported": True, "imported_confirmed": True}],
        "confirmed": lambda legal: legal.get("imported_confirmed") is True,
    },
    "shape": {
        "label": "the package shape",
        "action": "Tick the package shape",
        "field": "shape",
        "values": [{"shape": value, "shape_confirmed": True}
                   for value in ("rectangular", "cylindrical", "other")],
        "confirmed": lambda legal: legal.get("shape_confirmed") is True,
    },
}

UNDECIDED = (Verdict.INCONCLUSIVE, Verdict.UNVERIFIED)


@dataclass
class Unblocker:
    """One fact an officer can confirm, and the checks it is holding up."""

    key: str
    label: str
    action: str
    field: str
    rule_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rule_ids)


def pending(engine, scan, package, declarations, measurements, findings, *, as_of=None
           ) -> list[Unblocker]:
    """Facts not yet confirmed, ranked by how many checks each would release."""
    blocked = {finding.rule_id for finding in findings if finding.verdict in UNDECIDED}
    if not blocked:
        return []
    legal = getattr(package, "legal_context", {}) or {}
    output: list[Unblocker] = []
    for key, spec in FACTS.items():
        if spec["confirmed"](legal):
            continue
        released: set[str] = set()
        for assumption in spec["values"]:
            trial = package.model_copy(update={"legal_context": {**legal, **assumption}})
            try:
                results = engine.evaluate_all(scan, trial, declarations, measurements,
                                              as_of=as_of)
            except Exception:  # noqa: BLE001 - a hypothetical must never break the page
                continue
            released |= {f.rule_id for f in results
                         if f.rule_id in blocked and f.verdict not in UNDECIDED}
        if released:
            output.append(Unblocker(key=key, label=spec["label"], action=spec["action"],
                                    field=spec["field"], rule_ids=sorted(released)))
    output.sort(key=lambda item: (-item.count, item.key))
    return output
