"""A nutrition table is a region of the label, not a list of phrases.

Excluding it line by line is a losing game. The recogniser glues "SaturatedFat"
into one token so a word-boundary pattern misses it; "Salt (as NaCl)" belongs to
no nutrient vocabulary anyone thinks to write down; and "12.30g" is a bare cell
carrying no nutrient word at all. Each of those was read off one real packet as
its net quantity or its identity, and every pattern added to stop one of them
merely promoted the next cell in the table.
"""
from __future__ import annotations

from tula.domain.enums import DeclarationClass as DC
from tula.domain.enums import Panel
from tula.extract import context
from tula.extract.pipeline import CUES
from tula.ocr.base import OcrLine


def line(text, bbox, confidence=0.95):
    located = OcrLine(text=text, bbox=bbox, confidence=confidence)
    located.frame = "back.png"
    located._capture_index = 0
    located._image_size = (720, 1280)
    return (Panel.BACK, located)


def nutrition_panel():
    """A table shaped like the one on the pack that prompted this."""
    rows = [
        ("NUTRITIONALINFORMATION PER100g(%RDA/SERVE)", 100),
        ("Serving Size:20g", 130),
        ("Energy", 160), ("564.48Kcal", 160),
        ("Carbohydrates", 190), ("44.04g", 190),
        ("Protein", 220), ("12.30g", 220),
        ("SaturatedFat", 250), ("17.22g", 250),
        ("Salt (as NaCl)", 280), ("3.17g", 280),
    ]
    out = []
    for text, top in rows:
        left = 300 if text[0].isdigit() else 60
        out.append(line(text, (left, top, left + 220, top + 22)))
    return out


def test_bare_value_cells_inside_the_table_are_not_net_quantities():
    """"12.30g" is protein. Nothing on that line says so."""
    lines = nutrition_panel()
    protein = next(item for item in lines if item[1].text == "12.30g")
    assert not context.quantity_allowed(lines, protein, CUES[DC.NET_QUANTITY])


def test_an_unlisted_nutrient_name_inside_the_table_is_not_the_commodity():
    """"Salt (as NaCl)" is in the commodity lexicon and is not this commodity."""
    lines = nutrition_panel()
    salt = next(item for item in lines if item[1].text.startswith("Salt"))
    assert context.in_nutrition_panel(lines, salt)


def test_a_declaration_printed_below_the_table_is_untouched():
    lines = [*nutrition_panel(),
             line("NET QUANTITY 400 g", (60, 420, 280, 442))]
    quantity = lines[-1]
    assert not context.in_nutrition_panel(lines, quantity)
    assert context.quantity_allowed(lines, quantity, CUES[DC.NET_QUANTITY])


def test_a_declaration_printed_beside_the_table_is_untouched():
    """Containment is tested on both axes, not on the vertical band alone.

    A two-column back panel puts the MRP block level with the nutrition rows.
    Excluding the whole band would take the declaration with the table.
    """
    lines = [*nutrition_panel(), line("MRP 135/-", (560, 190, 700, 212))]
    beside = lines[-1]
    assert not context.in_nutrition_panel(lines, beside)


def test_a_passing_mention_does_not_create_a_table():
    """Below the row threshold there is no region, only a word on a label."""
    lines = [line("Energy drink", (60, 100, 260, 122)),
             line("NET QUANTITY 250 ml", (60, 140, 300, 162))]
    quantity = lines[-1]
    assert context.nutrition_panels(lines) == []
    assert context.quantity_allowed(lines, quantity, CUES[DC.NET_QUANTITY])


def test_an_oil_code_legend_is_not_the_commodity_name():
    """"Rice Bran Oil (RB)" decodes a batch letter; it is not what is inside.

    Read as a generic name it contradicted the real "Namkeen" on the front and
    the identity was withheld.
    """
    lines = [line("For oil used see the last character of batch no.", (60, 100, 400, 122)),
             line("Rice Bran Oil (RB)", (60, 130, 260, 152))]
    legend = lines[-1]
    assert context.CODE_LEGEND.search(legend[1].text)
    assert context.generic_context_blocked(
        lines, legend, disqualifiers=context.PROMOTION)
