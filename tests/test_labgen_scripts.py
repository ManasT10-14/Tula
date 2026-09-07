"""Generated script claims must be supported by real glyphs and shaping."""

from pathlib import Path

import pytest
from PIL import Image, ImageFont

from tula import labgen

HINDI = "शुद्ध वजन 200 g"


def test_hindi_font_has_real_glyphs_and_shapes_the_conjunct():
    font = labgen._font(True, 48, HINDI)
    from reportlab.pdfbase.ttfonts import TTFont

    cmap = TTFont("IndependentScriptCheck", font.path, subfontIndex=font.index).face.charToGlyph
    assert all(cmap.get(ord(c), 0) > 0 for c in HINDI if not c.isspace())
    assert font.layout_engine == ImageFont.Layout.RAQM
    missing = bytes(font.getmask("\U0010ffff"))
    assert bytes(font.getmask("श")) != missing
    assert bytes(font.getmask("व")) != missing
    basic = ImageFont.truetype(font.path, 48, index=font.index, layout_engine=ImageFont.Layout.BASIC)
    assert bytes(font.getmask("शुद्ध")) != bytes(basic.getmask("शुद्ध"))


@pytest.mark.skipif(not Path(r"C:\Windows\Fonts\Nirmala.ttc").is_file(), reason="Windows collection regression")
def test_windows_bold_uses_nirmala_collection_face_one():
    font = labgen._font(True, 48, HINDI)
    assert font.getname() == ("Nirmala UI", "Bold")
    assert font.index == 1


def test_generated_hindi_pixels_match_supported_shaped_text_and_sidecar(tmp_path):
    result = labgen.render(labgen.LabelSpec(), tmp_path)
    line = next(line for line in result.lines if line.text == HINDI)
    font = labgen._font_for_cap_height(True, 36, HINDI)
    mask = Image.frombytes("L", font.getmask(HINDI).size, bytes(font.getmask(HINDI)))
    with Image.open(result.png) as image:
        drawn = image.crop(line.bbox).convert("L")
    assert drawn.size == mask.size
    assert drawn.tobytes() == bytes(255 - p for p in mask.tobytes())
    assert HINDI in result.fixture.read_text(encoding="utf-8")
    assert 0 < result.truth.net_qty_cap_height_mm <= 3


@pytest.mark.parametrize("problem", ["missing", "invalid_font", "latin_only_font"])
def test_no_font_support_fails_without_writing_false_hindi_sidecar(tmp_path, monkeypatch, problem):
    candidates = []
    if problem == "invalid_font":
        path = tmp_path / "broken.ttf"
        path.write_bytes(b"This is not a font")
        candidates = [(str(path), 0)]
    elif problem == "latin_only_font":
        latin = next((path for path in [r"C:\Windows\Fonts\arial.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] if Path(path).is_file()), None)
        if latin is None:
            pytest.skip("No test Latin font installed")
        candidates = [(latin, 0)]
    monkeypatch.setattr(labgen, "DEVANAGARI_BOLD_CANDIDATES", candidates)
    monkeypatch.setattr(labgen, "DEVANAGARI_REGULAR_CANDIDATES", candidates)
    with pytest.raises(ValueError, match="Noto Sans Devanagari"):
        labgen.render(labgen.LabelSpec(), tmp_path, "unsupported")
    assert not (tmp_path / "unsupported.png").exists()
    assert not (tmp_path / "unsupported.txt").exists()
    assert not (tmp_path / "unsupported.meta.json").exists()


def test_missing_shaper_is_actionable_even_when_font_is_cached(tmp_path, monkeypatch):
    labgen._font(True, 48, HINDI)
    monkeypatch.setattr(labgen.features, "check_feature", lambda name: False)
    with pytest.raises(ValueError, match="RAQM"):
        labgen.render(labgen.LabelSpec(), tmp_path)
    assert not list(tmp_path.glob("*.txt"))


def test_latin_only_generation_does_not_require_hindi_fonts_or_shaping(tmp_path, monkeypatch):
    monkeypatch.setattr(labgen, "DEVANAGARI_BOLD_CANDIDATES", [])
    monkeypatch.setattr(labgen, "DEVANAGARI_REGULAR_CANDIDATES", [])
    monkeypatch.setattr(labgen.features, "check_feature", lambda name: False)
    result = labgen.render(labgen.LabelSpec(hindi_net_qty=False), tmp_path)
    assert result.png.is_file()
    assert "Net Wt. 200 g" in result.fixture.read_text(encoding="utf-8")
    assert "शुद्ध" not in result.fixture.read_text(encoding="utf-8")


def test_custom_hindi_text_cannot_bypass_font_check_when_quantity_is_latin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(labgen, "DEVANAGARI_BOLD_CANDIDATES", [])
    monkeypatch.setattr(labgen, "DEVANAGARI_REGULAR_CANDIDATES", [])
    with pytest.raises(ValueError, match="No installed font covers"):
        labgen.render(labgen.LabelSpec(brand="नमस्ते", hindi_net_qty=False), tmp_path)


def test_blank_scenario_does_not_claim_or_require_rendered_hindi(tmp_path, monkeypatch):
    monkeypatch.setattr(labgen, "DEVANAGARI_BOLD_CANDIDATES", [])
    monkeypatch.setattr(labgen, "DEVANAGARI_REGULAR_CANDIDATES", [])
    result = labgen.render(labgen.LabelSpec(blank_panel=True), tmp_path)
    assert result.lines == []
    assert "शुद्ध" not in result.fixture.read_text(encoding="utf-8")
