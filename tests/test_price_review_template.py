"""Do not attribute machine price hypotheses to an officer correction."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_observation(method, *, score=None, reason=None):
    item = {"raw": "20", "value": {"value": 20, "currency": "INR", "currency_verified": False},
            "sources": [{"text": "20", "frame": "label.png", "bbox": [50, 20, 110, 55],
                         "image_width": 500, "image_height": 500}],
            "status": "needs_review", "method": method, "ocr_confidence": score,
            "extraction_confidence": None, "review_reason": reason}
    analysis = SimpleNamespace(intelligence={"fields": {"retail_sale_price": [item]}},
        scan=SimpleNamespace(scan_id="PRICE-QA", frames=["label.png"]),
        review=SimpleNamespace(status="in_review", revision=0))
    environment = Environment(loader=FileSystemLoader(Path(__file__).parents[1] / "src/tula/web/templates"),
                              autoescape=select_autoescape(["html"]))
    return environment.get_template("_intelligence.html").render(a=analysis)


@pytest.mark.parametrize("score", [None, .93])
def test_machine_price_candidate_is_not_an_officer_correction(score):
    html = render_observation("pixel_currency_context_candidate", score=score,
        reason="Verify amount <script>alert(1)</script> and currency before recording a declaration.")
    assert "Manual verification required" in html
    assert "Currency is unverified" in html and "INR is a visual hypothesis" in html
    assert "The inspector supplied this correction" not in html
    assert "No automated extraction score was recorded" in html
    assert '<a href="#review">Correct a declaration</a>' in html and "Retail sale price" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)</script>" not in html
    assert 'href="/inspections/PRICE-QA/frames/0"' in html
    assert "Correct this observation" not in html
    if score is not None:
        assert "This score concerns the amount reading; it does not verify the currency" in html


def test_actual_officer_correction_retains_explicit_attribution():
    html = render_observation("inspector_correction", score=.81)
    assert "The inspector supplied this correction" in html and "Original OCR" in html
    assert "INR is a visual hypothesis" not in html


def test_other_machine_observation_without_score_has_neutral_attribution():
    html = render_observation("date_pattern")
    assert "No automated extraction score was recorded" in html
    assert "The inspector supplied this correction" not in html
