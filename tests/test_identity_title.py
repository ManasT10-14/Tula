"""Unresolved identity candidates must not become an unqualified page title."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tula.domain.enums import DeclarationClass as DC
from tula.domain.models import Analysis, Declaration, PackageFacts, Scan
from tula.report import render
from tula.security import User


@pytest.mark.parametrize("brand,commodity,title", [
    (True, False, "Package inspection"),
    (True, True, "Instant coffee"),
    (False, False, "Known &amp; Co"),
])
def test_title_uses_supported_identity_and_retains_candidate_evidence(brand, commodity, title):
    raw = "UNRESOLVED TOKEN" if brand else "Known & Co"
    declarations = {DC.BRAND: Declaration(klass=DC.BRAND, raw=raw,
        norm={"name": None, "requires_review": True, "candidates": [{"name": raw}]}
        if brand else {"name": "known & co"})}
    if commodity:
        declarations[DC.GENERIC_NAME] = Declaration(klass=DC.GENERIC_NAME,
            raw="Instant coffee", norm={"name": "instant coffee"})
    a = Analysis(scan=Scan(scan_id="TITLE-QA", inspector_id="owner"),
                 package=PackageFacts(), declarations=declarations)
    owner = User("owner", "owner", "QA Inspector", "inspector", True)
    request = SimpleNamespace(state=SimpleNamespace(user=owner, csrf_token="csrf"),
                              url=SimpleNamespace(path="/inspections/TITLE-QA"))
    env = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "src/tula/web/templates"),
                      autoescape=select_autoescape(["html"]))
    html = env.get_template("inspection.html").render(a=a, request=request,
        sections=render.build(a), integrity=[], headline=render.headline(a), history=[],
        shrinkflation=[], declaration_labels=render.DECLARATION_LABEL,
        finding_rows=lambda finding: render.finding_rows(finding, a),
        finding_message=render.finding_message,
        group_findings=render.group_findings,
        # This page's "do this next" panel is app state, not render state;
        # an empty answer is the honest one for a bare template environment.
        pending_facts=lambda _: [], needs_scale=lambda _: [],
        verdict_class=lambda _: "warn", verdict_label=render.VERDICT_LABEL,
        csrf_token="csrf", asset_url=lambda path: f"/static/{path}")
    assert f"<h1>{title}</h1>" in html
    if brand:
        assert "UNRESOLVED TOKEN" in html
        assert "<h1>UNRESOLVED TOKEN</h1>" not in html
        assert a.declarations[DC.BRAND].raw == raw
    if title == "Package inspection":
        assert "Package identity needs verification" in html
