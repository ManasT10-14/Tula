"""The rule specification format.

A rule's versioned declarations, sources and decision parameters live in data.
Python interprets these parameters and explicitly gates unresolved legal context:

  * a legal officer has to be able to read and amend it;
  * the report has to quote the clause that produced each verdict;
  * manufacture, packing, import and sale can have different legal dates;
    the assessment records its date basis and unresolved transitions.

`effective_from` / `effective_to` are what make the third point work.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import AssuranceTier, DeclarationClass, Panel, Severity

Expr = dict[str, Any] | str | bool | int | float | list | None  # evaluated by evaluator.py


class RuleCitation(BaseModel):
    act: str = "Legal Metrology Act, 2009"
    rules: str = "Legal Metrology (Packaged Commodities) Rules, 2011"
    clause: str
    text: str = ""


class Messages(BaseModel):
    """Report text. Written for the respondent, not for the developer."""

    model_config = ConfigDict(populate_by_name=True)

    passed: str = Field("", alias="pass")
    violation: str = ""
    inconclusive: str = ""


class Rule(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    citation: RuleCitation
    # What this rule is about, in the words an inspector would use to a
    # shopkeeper. The citation text is a screening paraphrase of the statute and
    # reads like one; it belongs in the record and on the notice, not as the
    # first thing on screen. Sixteen findings whose headings all begin "Rule
    # 6(1)(c), Rule 13" are sixteen findings nobody reads. Data, not code,
    # because it is amended alongside the clause it describes.
    plain: str = ""
    # The one thing to do about this rule when it is not decided, phrased as an
    # instruction. Empty where the verdict already carries its own next step.
    plain_action: str = ""
    effective_from: date
    effective_to: date | None = None

    declaration: DeclarationClass | None = None
    applies_when: Expr = True
    assertion: Expr = Field(True, alias="assert")

    severity: Severity = Severity.MAJOR
    min_assurance_tier: AssuranceTier = AssuranceTier.C
    # Panels this declaration must lawfully appear on. Photographing them is
    # then enough to prove the declaration absent -- Rule 7 puts the net
    # quantity on the principal display panel, so a legible PDP without one is a
    # violation even from a single frame. Left empty, the declaration may be
    # grouped on any face and only a complete capture can establish absence.
    # This is a statutory question, so it lives in the pack rather than in code.
    absence_provable_from: list[Panel] = Field(default_factory=list)
    penalty_ref: str | None = None
    messages: Messages = Field(default_factory=Messages)
    evidence: list[str] = Field(default_factory=list)
    tests: str | None = None
    sources: list[dict[str, str]] = Field(default_factory=list)
    legal_check: dict[str, Any] = Field(default_factory=dict)

    def in_force_on(self, when: date) -> bool:
        if when < self.effective_from:
            return False
        return not (self.effective_to is not None and when > self.effective_to)


class RulePack(BaseModel):
    """A versioned, hashable set of rules.

    `version` is what gets recorded on every finding, and what
    `GET /v1/rules/{version}` serves back so any verdict is reproducible.
    """

    version: str
    title: str
    source: str = ""
    notes: str = ""
    rules: list[Rule] = Field(default_factory=list)
    legal_policy: dict[str, Any] = Field(default_factory=dict)

    def in_force_on(self, when: date) -> list[Rule]:
        return [r for r in self.rules if r.in_force_on(when)]

    def by_id(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


def load_pack(directory: str | Path) -> RulePack:
    """Load a rule pack from a directory of JSON files.

    `pack.json` carries the manifest; every other `*.json` is one rule. Keeping
    one rule per file means a git diff on an amendment shows exactly what a
    notification changed.
    """

    directory = Path(directory)
    manifest_path = directory / "pack.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no pack.json in {directory}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "pack.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            rules.append(Rule.model_validate(raw))
        except Exception as exc:  # pragma: no cover - surfaced during authoring
            raise ValueError(f"{path.name}: {exc}") from exc

    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate rule ids in {directory}: {sorted(dupes)}")

    return RulePack(rules=rules, **manifest)
