"""Build the SIH 2026 Idea Presentation for PS 26034 on the official template.

Every figure that appears on a slide is traceable to a file in this repository;
nothing here is estimated or rounded up. Run:  python build_deck.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches

sys.path.insert(0, str(Path(__file__).parent))
from deck_lib import (  # noqa: E402
    AMBER, BLUE, CW, DEEP, DEVA, GREEN, GREY, HEAD, INK, LINE, LM, MUTED, NAVY,
    PALE, PALE2, RED, STEEL, WHITE, badge, box, bullets, card, chevron,
    delete_shape, delete_slide, down_arrow, eyebrow, pill, rule_line,
    set_team_badge, set_title, strip_body, text,
)

ROOT = Path(r"C:\Users\offic\OneDrive\Desktop\sih")
TEMPLATE = ROOT / "SIH2026-IDEA-Presentation-Format.pptx"
OUT = ROOT / "SIH2026-Tula-PS26034-Idea-Presentation.pptx"

# --- the values only the SIH portal can supply -------------------------------
# Left as visible fill-ins rather than guessed. Edit these four strings and
# re-run to stamp them through the deck.
TEAM_NAME = "\u2039 Team name from portal \u203a"
TEAM_ID = "\u2039 Team ID \u203a"
THEME = "\u2039 Theme as listed on the portal \u203a"
BADGE = "Your\nTeam\nName"

PS_ID = "26034"
PS_TITLE = ("Software System to check compliance of Packaged Commodities under "
            "Legal Metrology (Packaged Commodities) Rules, 2011 by scanning "
            "products, images and labels")
PS_CATEGORY = "Software"
ORG = ("Ministry of Consumer Affairs, Food & Public Distribution "
       "\u00b7 Department of Consumer Affairs")

FILL = AMBER            # portal fill-ins, so they cannot be missed
LIGHT = RGBColor(0xC9, 0xD8, 0xE8)
AMBER_PALE = RGBColor(0xFD, 0xF5, 0xE6)
AMBER_LINE = RGBColor(0xE4, 0xCF, 0xA4)


def label(slide, x, y, w, s, *, color=MUTED, size=7.8):
    return text(slide, x, y, w, 0.16, s.upper(), size=size, bold=True,
                color=color, spacing=1.15)


def value(slide, x, y, w, h, s, *, size=13.5, color=INK, line=0.95):
    return text(slide, x, y, w, h, s, size=size, bold=True, color=color, line=line)


def marker(slide, x, y, color=BLUE, s=0.075):
    return box(slide, x, y, s, s, fill=color, shape=MSO_SHAPE.RECTANGLE)


def lead(slide, x, y, w, h, head, rest, *, size=9.5):
    """A bold lead-in followed by one short clarifying clause."""
    marker(slide, x, y + 0.045)
    return text(slide, x + 0.22, y, w - 0.22, h,
                [[(head, {"bold": True, "color": DEEP}), (" " + rest, {})]],
                size=size, color=INK, line=1.12)


# ============================================================== SLIDE 1 =====
def slide1(prs):
    s = prs.slides[0]
    for sh in list(s.shapes):
        if sh.name in ("Subtitle 3", "TextBox 9"):
            delete_shape(sh)

    x, w = 0.62, 5.48
    rule_line(s, x, 1.72, 1.05, NAVY, 0.038)

    tb = text(s, x, 1.88, w, 0.60,
              [[("TULA", {"size": 36, "font": HEAD, "color": NAVY, "bold": True}),
                ("  \u0924\u0941\u0932\u093e", {"size": 26, "font": DEVA,
                                                "color": BLUE, "bold": True})]],
              line=0.9)
    tb.text_frame.word_wrap = False

    text(s, x, 2.46, w, 0.30,
         "A compliance engine for the Legal Metrology (Packaged Commodities) Rules, 2011",
         size=10.5, color=MUTED, line=1.0)

    rule_line(s, x, 2.86, w, LINE)

    xa, wa = x, 2.55
    xb, wb = x + 2.93, 2.55

    label(s, xa, 3.02, wa, "Problem Statement ID")
    value(s, xa, 3.20, wa, 0.28, PS_ID)
    label(s, xb, 3.02, wb, "PS Category")
    value(s, xb, 3.20, wb, 0.28, PS_CATEGORY)

    label(s, x, 3.62, w, "Problem Statement Title")
    value(s, x, 3.80, w, 0.62, PS_TITLE, size=11, line=1.12)

    label(s, xa, 4.56, wa, "Theme")
    value(s, xa, 4.74, wa, 0.30, THEME, size=11, color=FILL, line=1.05)
    label(s, xb, 4.56, wb, "Team ID")
    value(s, xb, 4.74, wb, 0.30, TEAM_ID, size=11, color=FILL, line=1.05)

    label(s, x, 5.16, w, "Team Name (registered on portal)")
    value(s, x, 5.34, w, 0.30, TEAM_NAME, size=11, color=FILL, line=1.05)

    rule_line(s, x, 5.86, w, LINE)
    text(s, x, 5.98, w, 0.40, ORG, size=9, color=MUTED, line=1.15)


# ============================================================== SLIDE 2 =====
def slide2(prs):
    """Proposed solution, carried by the decision flowchart that is the idea."""
    s = prs.slides[1]
    strip_body(s)
    set_title(s, "TULA", size=34, devanagari="  \u0924\u0941\u0932\u093e")
    set_team_badge(s, BADGE)
    for sh in s.shapes:
        if sh.is_placeholder and sh.name.startswith("Title"):
            sh.height = Inches(0.80)

    text(s, 2.60, 0.93, 8.10, 0.26,
         "A COMPLIANCE ENGINE FOR THE LEGAL METROLOGY "
         "(PACKAGED COMMODITIES) RULES, 2011",
         size=9, bold=True, color=MUTED, spacing=1.05, align=PP_ALIGN.CENTER)

    eyebrow(s, LM, 1.30, "Proposed solution \u00b7 detailed explanation", w=CW)
    text(s, LM, 1.58, CW, 0.36,
         [[("Today ", {"bold": True, "color": DEEP}),
           ("an officer checks declarations, letter heights and panel area by eye "
            "and by ruler.  ", {"color": MUTED}),
           ("Tula ", {"bold": True, "color": DEEP}),
           ("takes the photograph through the decision below and returns one of "
            "seven verdicts per rule \u2014 each carrying its clause, its crop and "
            "how far the evidence may be trusted.", {})]],
         size=11, color=INK, line=1.08)

    # ------------------------------------------------ the decision flowchart --
    #   photograph -> applicability -> exemption -> assertion -> three gates
    #   with every drop-out drawn, so all seven verdicts are visible at once.
    fy, fh = 2.06, 0.86
    cols = [(0.55, 1.05), (1.69, 1.75), (3.53, 1.75), (5.37, 2.60), (8.06, 3.20),
            (11.35, 1.42)]

    x0, w0 = cols[0]
    box(s, x0, fy, w0, fh, fill=NAVY, radius=0.10)
    text(s, x0 + 0.06, fy, w0 - 0.12, fh,
         [{"t": "ONE", "size": 10.5, "bold": True, "color": WHITE},
          {"t": "PHOTO", "size": 10.5, "bold": True, "color": WHITE},
          {"t": "of the pack", "size": 7, "color": STEEL, "space_before": 3}],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line=0.94)

    steps = [
        (1, "IS THIS RULE IN FORCE?",
         "commencement date and\nChapter II scope for this\npackage"),
        (2, "IS THE DUTY RELIEVED?",
         "Rule 3 and Rule 26 \u2014 small\npacks, tobacco, institutional\nor industrial purchase"),
        (3, "EVALUATE THE ASSERTION",
         "Kleene three-valued evaluation over the\ntrusted declarations and the measured\nmillimetres, guard-banded at k = 2"),
    ]
    for (num, head, detail), (cx, cw) in zip(steps, cols[1:4]):
        card(s, cx, fy, cw, fh, fill=WHITE, line=STEEL, accent=BLUE)
        badge(s, cx + 0.20, fy + 0.26, num, d=0.19)
        text(s, cx + 0.36, fy + 0.17, cw - 0.48, 0.18, head, size=8, bold=True,
             color=NAVY, spacing=0.5)
        text(s, cx + 0.12, fy + 0.42, cw - 0.24, 0.40, detail, size=7.5,
             color=MUTED, line=1.10)

    gx, gw = cols[4]
    card(s, gx, fy, gw, fh, fill=PALE, line=STEEL, accent=NAVY)
    badge(s, gx + 0.20, fy + 0.20, 4, d=0.19)
    text(s, gx + 0.36, fy + 0.11, gw - 0.48, 0.18,
         "THREE INDEPENDENT GATES", size=8, bold=True, color=NAVY, spacing=0.5)
    gates = [("COVERAGE", "may we say a declaration is absent?"),
             ("TIER", "how well do we know the millimetre?"),
             ("LANE", "may this evidence convict at all?")]
    for i, (gname, gq) in enumerate(gates):
        gy = fy + 0.34 + i * 0.165
        text(s, gx + 0.14, gy, 0.72, 0.15, gname, size=7, bold=True, color=DEEP,
             spacing=0.4)
        text(s, gx + 0.88, gy, gw - 1.02, 0.15, gq, size=7, color=MUTED)

    vx, vw = cols[5]
    box(s, vx, fy, vw, fh, fill=RED, radius=0.10)
    text(s, vx + 0.06, fy, vw - 0.12, fh,
         [{"t": "VIOLATION", "size": 10, "bold": True, "color": WHITE},
          {"t": "clause, crop and\nuncertainty attached", "size": 7,
           "color": RGBColor(0xF0, 0xD6, 0xD2), "space_before": 3}],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line=0.98)

    for i in range(len(cols) - 1):
        cxa = cols[i][0] + cols[i][1]
        chevron(s, cxa + (cols[i + 1][0] - cxa) / 2, fy + fh / 2, size=0.14)

    # ----------------------------------------------- the six drop-out verdicts --
    ay, ah = 2.94, 0.20
    chy, chh = 3.16, 0.28
    lby = 3.48

    def drop(cx, cw, name, colour, why, size=8):
        down_arrow(s, cx + cw / 2, ay, ah)
        box(s, cx, chy, cw, chh, fill=WHITE, line=colour, lw=1.1, radius=0.5)
        text(s, cx, chy, cw, chh, name, size=size, bold=True, color=colour,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, cx, lby, cw, 0.18, why, size=7, color=MUTED, italic=True,
             align=PP_ALIGN.CENTER)

    drop(cols[1][0], cols[1][1], "NOT APPLICABLE", GREY, "not in force")
    drop(cols[2][0], cols[2][1], "EXEMPT", GREY, "relieved")

    tx, tw = cols[3]
    third = (tw - 2 * 0.08) / 3
    for i, (name, colour, why) in enumerate(
            [("PASS", GREEN, "holds"),
             ("INCONCLUSIVE", BLUE, "straddles the limit"),
             ("UNVERIFIED", GREY, "input missing")]):
        drop(tx + i * (third + 0.08), third, name, colour, why, size=7)

    drop(gx, gw, "ADVISORY", AMBER, "any one gate demotes the outcome")

    text(s, LM, 3.76, CW, 0.24,
         [[("A gate can only demote an outcome, never promote it.", {"bold": True, "color": DEEP}),
           ("  A rule can fail on the label and still, correctly, not become a "
            "violation \u2014 because a system that can only say PASS or VIOLATION "
            "is forced to guess, and a wrong accusation costs far more than a "
            "deferred one.", {})]],
         size=9.5, color=MUTED, line=1.1)

    # ---------------------------------------- the two remaining pointers ------
    cw2 = 5.95
    eyebrow(s, LM, 4.18, "How it addresses the problem", w=cw2)
    eyebrow(s, 6.83, 4.18, "Innovation and uniqueness", w=cw2)

    left = [
        ("All 18 rules, not a checklist.",
         "Manufacturer, generic name, net quantity, date, MRP, consumer care, unit "
         "sale price, country of origin, letter height and panel grouping."),
        ("Products, images and labels \u2014 each on its own footing.",
         "A field capture, a marketplace listing, a citizen's snap and the packer's "
         "own artwork enter by different lanes and carry different weight."),
        ("Ends in an actionable record, not a score.",
         "Clause-cited PDF, editable DOCX and JSON with the crops; the inspector "
         "drafts and an independent supervisor approves."),
    ]
    right = [
        ("Seven verdicts, not two.",
         "\u201cI cannot tell from this photograph\u201d becomes a first-class, "
         "actionable answer instead of a guess in either direction."),
        ("No ruler, and no scale needed in order to abstain.",
         "Panel area is bracketed from the photograph itself \u2014 the interval "
         "held the truth 8 times in 8 \u2014 and a size sweep settles the rules "
         "whose outcome cannot depend on scale."),
        ("Rules are dated data, not code.",
         "Reading the Gazette ourselves withdrew a bilingual rule the brief had "
         "asked for and corrected a letter height from 1.5 mm to 2.0 mm."),
    ]
    for col_x, items in ((LM, left), (6.83, right)):
        yy = 4.50
        for head, rest in items:
            lead(s, col_x, yy, cw2, 0.60, head, rest, size=9.5)
            yy += 0.78


# ============================================================== SLIDE 3 =====
def slide3(prs):
    """Technical approach: the layered architecture, naming its technologies."""
    s = prs.slides[2]
    strip_body(s)
    set_title(s, "TECHNICAL APPROACH", size=30)
    set_team_badge(s, BADGE)

    eyebrow(s, LM, 1.28,
            "Technologies to be used \u00b7 methodology and process for "
            "implementation", w=CW)

    aw = 9.30                       # architecture column
    sx, sw = 10.05, 2.73            # side column
    ly, lh, gap = 1.60, 0.62, 0.145

    layers = [
        ("USERS AND LANES",
         ["Inspector \u00b7 field", "Supervisor \u00b7 approval",
          "Packer \u00b7 pre-market", "Citizen \u00b7 advisory"],
         "photograph + the lane it was taken in"),
        ("APPLICATION",
         ["FastAPI \u00b7 Uvicorn", "Jinja2 + HTMX 1.9",
          "scrypt auth \u00b7 3 roles", "Durable job queue"],
         "image bytes, queued with an owned lease"),
        ("VISION \u2014 CPU ONLY",
         ["RapidOCR PP-OCRv4 ONNX", "Tesseract 5 \u00b7 eng+hin",
          "zxing-cpp \u00b7 GTIN", "OpenCV-contrib ArUco"],
         "recognised text, boxes and confidences"),
        ("EXTRACT AND MEASURE",
         ["9 declaration parsers", "found vs trusted filter",
          "glyph cap-height", "panel area bracket"],
         "located declarations and millimetres \u00b1 u"),
        ("DECIDE",
         ["18 versioned JSON rules", "Kleene 3-valued engine",
          "guard band at k = 2", "tier \u00b7 coverage \u00b7 lane"],
         "one graded finding per rule"),
        ("OUTPUT AND APPROVAL",
         ["ReportLab PDF + crops", "python-docx \u00b7 editable",
          "JSON analysis", "second-officer approval"],
         None),
    ]
    for i, (name, chips, handoff) in enumerate(layers):
        y = ly + i * (lh + gap)
        card(s, LM, y, aw, lh, fill=WHITE, line=LINE)
        box(s, LM, y, 0.075, lh, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
        text(s, LM + 0.20, y, 1.62, lh, name, size=8.5, bold=True, color=NAVY,
             spacing=0.5, anchor=MSO_ANCHOR.MIDDLE, line=1.02)
        rule_line(s, LM + 1.92, y + 0.11, 0.012, LINE, lh - 0.22)
        cw_chip = (aw - 2.10 - 0.16 - 3 * 0.10) / 4
        for j, chip in enumerate(chips):
            pill(s, LM + 2.06 + j * (cw_chip + 0.10), y + 0.155, cw_chip, 0.31,
                 chip, fill=PALE, line=LINE, color=DEEP, size=7.5)
        if handoff:
            down_arrow(s, LM + 1.00, y + lh, gap, color=STEEL, head=0.085)
            text(s, LM + 1.16, y + lh + 0.005, 5.6, 0.14, handoff, size=7,
                 color=MUTED, italic=True)

    bottom = ly + 6 * (lh + gap) - gap        # 6.02

    # the store every layer writes to, drawn beside them rather than wired in
    card(s, sx, ly, sw, 2.30, fill=PALE2, line=LINE, accent=NAVY)
    text(s, sx + 0.15, ly + 0.16, sw - 0.30, 0.18, "EVIDENCE STORE", size=8.5,
         bold=True, color=NAVY, spacing=0.9)
    text(s, sx + 0.15, ly + 0.38, sw - 0.30, 0.16, "written by every layer above",
         size=7, color=BLUE, italic=True)
    rule_line(s, sx + 0.15, ly + 0.60, sw - 0.30, STEEL)
    bullets(s, sx + 0.15, ly + 0.70, sw - 0.30, 1.75,
            ["SQLite (WAL) \u00b7 26 tables",
             "SHA-256 on every original and working copy",
             "Append-only audit of who decided what",
             "Rule-pack archive and rollback",
             "Missing or altered evidence blocks the report"],
            size=7.5, gap=3)

    py = ly + 2.30 + 0.16
    card(s, sx, py, sw, bottom - py, fill=WHITE, line=LINE, accent=GREEN)
    text(s, sx + 0.15, py + 0.16, sw - 0.30, 0.18, "WORKING PROTOTYPE", size=8.5,
         bold=True, color=GREEN, spacing=0.9)
    text(s, sx + 0.15, py + 0.38, sw - 0.30, 0.16, "running, not a mock-up",
         size=7, color=BLUE, italic=True)
    rule_line(s, sx + 0.15, py + 0.60, sw - 0.30, STEEL)
    bullets(s, sx + 0.15, py + 0.70, sw - 0.30, 1.30,
            ["Local console runs capture \u2192 review \u2192 decide \u2192 "
             "approve \u2192 export",
             "1,556 automated tests pass",
             "Wheel installs clean outside the checkout",
             "53-image audit \u00b7 40 of 40 checks passed"],
            size=7.5, gap=3)

    cy = 6.18
    box(s, LM, cy, CW, 0.46, fill=DEEP, radius=0.22)
    text(s, LM + 0.22, cy, CW - 0.44, 0.46,
         [[("Inference is CPU-only by design.  ", {"bold": True, "color": WHITE}),
           ("No GPU anywhere on the serving path, no cloud round trip for a check "
            "made on a shop floor \u2014 the whole pipeline runs offline on the "
            "district-office laptop that is already there.", {"color": LIGHT})]],
         size=9.5, anchor=MSO_ANCHOR.MIDDLE, line=1.05)


# ============================================================== SLIDE 4 =====
def slide4(prs):
    s = prs.slides[3]
    strip_body(s)
    set_title(s, "FEASIBILITY AND VIABILITY", size=30)
    set_team_badge(s, BADGE)

    eyebrow(s, LM, 1.30,
            "Analysis of feasibility \u00b7 potential challenges and risks \u00b7 "
            "strategies for overcoming them", w=CW)

    cols = [(0.55, 2.28, "Potential challenge"),
            (2.95, 2.72, "Why it is hard"),
            (5.79, 4.14, "Our strategy for overcoming it"),
            (10.05, 2.73, "Feasibility evidence")]
    for cx, cwd, head in cols:
        text(s, cx, 1.68, cwd, 0.18, head.upper(), size=7.8, bold=True,
             color=NAVY, spacing=1.1)
    rule_line(s, LM, 1.90, CW, STEEL)

    rows = [
        ("A photograph has no scale",
         "An image records angles, not distances, so a small pack close up and a "
         "large one further away give the same picture. Across our 14 real "
         "photographs, 0 carried an EXIF subject distance and 12 carried no EXIF.",
         "A printed ArUco card in frame gives a Tier B millimetre. Panel area is "
         "bracketed from the image itself \u2014 text hull as floor, package edge "
         "as ceiling \u2014 and reported as an interval, never a point estimate. "
         "Where no scale reached the camera, we sweep every package size the "
         "declared quantity allows and report only the rules whose outcome cannot "
         "change.",
         "8 / 8", "the bracketed panel area contained the true value, at 35% "
         "median relative uncertainty"),
        ("Real packages defeat OCR",
         "Dot-matrix date codes, glare, curved packs and Devanagari all degrade "
         "recognition, and a confident wrong read is far worse than no read.",
         "Every value is scored found vs trusted. An untrusted value is retained "
         "as a review candidate and can never on its own establish a violation or "
         "an absence. Tesseract with hin data is the second recogniser for "
         "Devanagari, and the record says so explicitly when it is missing.",
         "105 / 109", "printed values recovered across 24 degraded labels \u00b7 "
         "0 wrong acceptances on 10 real photographs"),
        ("The Rules keep changing",
         "Clauses commence on different dates and some were later corrected. A "
         "hardcoded checker is wrong the day it ships, and silently rewrites past "
         "cases when it is upgraded.",
         "Rules are versioned JSON carrying temporal validity and a source "
         "citation, not Python. Superseded packs are archived, so a historical "
         "record keeps the verdicts it was actually decided under. Reading the "
         "Gazette ourselves withdrew one rule outright and corrected two more "
         "rather than ship them.",
         "18 rules \u00b7 24 sources", "pack 2026.09.07-legal-review-1, every "
         "clause linked to the official PDF it came from"),
        ("A wrong accusation is costly",
         "An automated finding could be mistaken for legal proof, and a "
         "marketplace listing image need not even depict the package that ships.",
         "Three independent gates, each able only to demote. Citizen and "
         "marketplace lanes are advisory by construction. The inspector drafts, an "
         "independent supervisor approves, every action is audited append-only, "
         "and no notice is ever issued automatically \u2014 the statutory decision "
         "stays with a human officer.",
         "1,556 tests", "full suite green \u00b7 wheel verified installing clean "
         "outside the checkout"),
    ]
    y, rh, gap = 1.97, 1.06, 0.055
    for i, (c1, c2, c3, metric, mcap) in enumerate(rows):
        if i % 2 == 0:
            box(s, LM, y, CW, rh, fill=PALE, radius=0.04)
        badge(s, cols[0][0] + 0.09, y + 0.17, i + 1, d=0.18)
        text(s, cols[0][0] + 0.26, y + 0.08, cols[0][1] - 0.26, 0.60, c1,
             size=10.5, bold=True, color=DEEP, line=1.06)
        text(s, cols[1][0], y + 0.08, cols[1][1] - 0.14, 0.90, c2, size=8.3,
             color=MUTED, line=1.1)
        text(s, cols[2][0], y + 0.08, cols[2][1] - 0.16, 0.90, c3, size=8.3,
             color=INK, line=1.1)
        card(s, cols[3][0], y + 0.07, cols[3][1], rh - 0.14, fill=WHITE,
             line=STEEL)
        text(s, cols[3][0] + 0.12, y + 0.15, cols[3][1] - 0.24, 0.26, metric,
             size=13.5, bold=True, color=NAVY, line=0.95)
        text(s, cols[3][0] + 0.12, y + 0.44, cols[3][1] - 0.24, 0.50, mcap,
             size=7.8, color=MUTED, line=1.1)
        y += rh + gap

    oy = 6.36
    card(s, LM, oy, CW, 0.46, fill=AMBER_PALE, line=AMBER_LINE)
    box(s, LM, oy, 0.075, 0.46, fill=AMBER, shape=MSO_SHAPE.RECTANGLE)
    text(s, LM + 0.24, oy, CW - 0.45, 0.46,
         [[("STILL OPEN BEFORE DEPLOYMENT   ",
            {"bold": True, "color": AMBER, "spacing": 1.0, "size": 8.5}),
           ("independent legal sign-off on the rule pack \u00b7 physical metrology "
            "validation of the printed scale card \u00b7 a representative "
            "multi-state OCR evaluation \u00b7 formal accessibility certification. "
            "We name these rather than claim they are done.", {})]],
         size=8.8, color=INK, anchor=MSO_ANCHOR.MIDDLE, line=1.1)


# ============================================================== SLIDE 5 =====
def slide5(prs):
    s = prs.slides[4]
    strip_body(s)
    set_title(s, "IMPACT AND BENEFITS", size=30)
    set_team_badge(s, BADGE)

    eyebrow(s, LM, 1.30, "Potential impact on the target audience", w=CW)

    users = [
        ("LEGAL METROLOGY\nINSPECTOR", "field lane \u00b7 may sustain a violation",
         ["Clause-cited findings, each with the crop it was read from",
          "No tape measure \u2014 panel size comes from the photograph",
          "Told when a second visit with a card would change nothing"]),
        ("CONTROLLER /\nSUPERVISOR", "approval lane",
         ["Approves or returns findings they did not contribute to",
          "District-level coverage on the dashboard",
          "Append-only record of who decided what, on which evidence"]),
        ("MANUFACTURER /\nPACKER", "pre-market lane \u00b7 Tier A, exact",
         ["Own artwork checked before the print run",
          "Read from vector source, so no estimation at all",
          "Fixed on screen rather than after a seizure"]),
        ("CONSUMER /\nCITIZEN", "citizen lane \u00b7 advisory referral",
         ["A shelf photograph becomes a referral for an inspector",
          "Deterministic allergen screen, FSS Labelling 2020",
          "Quotes the exact words it matched, never a guess"]),
    ]
    cwu = (CW - 3 * 0.17) / 4
    y, h = 1.62, 1.98
    for i, (role, lane, items) in enumerate(users):
        cx = LM + i * (cwu + 0.17)
        card(s, cx, y, cwu, h, fill=WHITE, line=LINE, accent=BLUE)
        text(s, cx + 0.14, y + 0.15, cwu - 0.28, 0.40, role, size=9.5, bold=True,
             color=NAVY, line=1.04)
        text(s, cx + 0.14, y + 0.58, cwu - 0.28, 0.16, lane, size=8,
             color=BLUE, italic=True)
        rule_line(s, cx + 0.14, y + 0.80, cwu - 0.28, LINE)
        bullets(s, cx + 0.14, y + 0.90, cwu - 0.28, 1.00, items, size=8, gap=4)

    eyebrow(s, LM, 3.80,
            "Benefits of the solution (social, economic, environmental)", w=CW)

    benefits = [
        ("SOCIAL", GREEN,
         ["One evidence standard in every district \u2014 a finding stops "
          "depending on which officer holds the ruler",
          "Hindi in Devanagari read on the same footing as English, because "
          "Rule 9(4) puts it there",
          "Allergen referrals reach the consumer instead of stopping at the file"]),
        ("ECONOMIC", NAVY,
         ["Fewer wrongful notices \u2014 the system abstains instead of guessing",
          "Fewer wasted second visits: it says outright when a re-capture would "
          "change nothing",
          "Defects caught in artwork, before a run is printed, filled and shipped"]),
        ("ENVIRONMENTAL", BLUE,
         ["A label defect caught pre-print is packaging that never has to be "
          "destroyed or over-stickered",
          "CPU-only inference adds no GPU procurement",
          "Runs on the office laptop already in the building"]),
    ]
    cwb = (CW - 2 * 0.19) / 3
    yb = 4.12
    for i, (head, colour, items) in enumerate(benefits):
        cx = LM + i * (cwb + 0.19)
        card(s, cx, yb, cwb, 1.82, fill=PALE, line=LINE)
        box(s, cx, yb, cwb, 0.055, fill=colour, shape=MSO_SHAPE.RECTANGLE)
        text(s, cx + 0.16, yb + 0.18, cwb - 0.32, 0.20, head, size=9.5,
             bold=True, color=colour, spacing=1.2)
        bullets(s, cx + 0.16, yb + 0.50, cwb - 0.32, 1.20, items, size=8.5,
                gap=5, line=1.12)

    cy = 6.14
    box(s, LM, cy, CW, 0.50, fill=DEEP, radius=0.20)
    text(s, LM + 0.22, cy, CW - 0.44, 0.50,
         [[("SYSTEM-LEVEL   ", {"bold": True, "color": WHITE, "spacing": 1.0,
                                "size": 8.5}),
           ("The Jan Vishwas (Amendment of Provisions) Act, 2026 replaced section "
            "36(1) with an improvement notice for a first offence, in force since "
            "1 May 2026. A screen that abstains rather than over-accuses, and "
            "leaves the statutory decision with a human officer, is what that "
            "regime asks of software.", {"color": LIGHT})]],
         size=9, anchor=MSO_ANCHOR.MIDDLE, line=1.08)


# ============================================================== SLIDE 6 =====
def slide6(prs):
    s = prs.slides[5]
    strip_body(s)
    set_title(s, "RESEARCH AND REFERENCES", size=30)
    set_team_badge(s, BADGE)

    eyebrow(s, LM, 1.30, "Details / links of the reference and research work",
            w=CW)

    cols = [
        ("PRIMARY LAW \u2014 OFFICIAL GAZETTE PDFs",
         "consumeraffairs.gov.in \u00b7 sansad.in \u00b7 fssai.gov.in",
         ["Legal Metrology (Packaged Commodities) Rules, 2011 \u2014 the base text",
          "2017 amendment and its November 2017 corrigendum \u2014 Table I letter "
          "heights; the corrigendum sets the first blown/formed cell to 2.0 mm",
          "G.S.R. 385(E), 2015 \u2014 Rule 6(2) consumer care, in force 1 Jan 2016",
          "G.S.R. 226(E) \u2014 prescribed unit-sale-price basis; Rule 6(11) from "
          "1 Jan 2024",
          "Rajya Sabha AU1181, 11 Feb 2022 \u2014 Rule 9(4): Hindi in Devanagari "
          "or English",
          "Jan Vishwas Act 2026, Sch. 66(N) with S.O. 2103(E) \u2014 section "
          "36(1), from 1 May 2026",
          "FSSAI Labelling and Display Regulations compendium \u2014 allergen "
          "declaration, reg. 5(3)"]),
        ("MEASUREMENT STANDARDS AND METHOD",
         "how a measurement becomes a defensible statement",
         ["ILAC-G8:09/2019 \u2014 decision rules and guard bands for statements of "
          "conformity. This is why a straddling measurement returns INCONCLUSIVE.",
          "JCGM 100:2008 (GUM) \u2014 expanded uncertainty; every millimetre on "
          "this deck is reported at k = 2",
          "Kleene three-valued logic \u2014 the basis for a verdict set that is "
          "allowed to say \u2018unknown\u2019",
          "Garrido-Jurado et al. \u2014 ArUco fiducial markers, the in-frame scale "
          "reference the scale card prints",
          "Guard banding applied to Table I of Rule 7(2): the acceptance interval "
          "is narrowed by the expanded uncertainty, so the system never convicts "
          "inside its own error bar"]),
        ("TECHNOLOGY REFERENCES",
         "everything on the serving path runs on CPU",
         ["PP-OCRv4 detection and recognition, served through RapidOCR on ONNX "
          "Runtime",
          "Tesseract 5 with hin traineddata \u2014 Devanagari recognition",
          "OpenCV-contrib cv2.aruco for markers; zxing-cpp for GTIN decoding",
          "FastAPI \u00b7 pydantic v2 \u00b7 SQLite (WAL) \u00b7 ReportLab \u00b7 "
          "python-docx",
          "pillow-heif \u2014 HEIC uploads straight from an inspector\u2019s "
          "phone; originals are never re-encoded",
          "Open Food Facts was deliberately NOT used: its robots.txt disallows "
          "/api for all agents"]),
    ]
    cwr = (CW - 2 * 0.19) / 3
    y, h = 1.62, 3.32
    for i, (head, sub, items) in enumerate(cols):
        cx = LM + i * (cwr + 0.19)
        card(s, cx, y, cwr, h, fill=WHITE, line=LINE, accent=NAVY)
        text(s, cx + 0.15, y + 0.16, cwr - 0.30, 0.34, head, size=8.5, bold=True,
             color=NAVY, spacing=0.9, line=1.08)
        text(s, cx + 0.15, y + 0.52, cwr - 0.30, 0.18, sub, size=8,
             color=BLUE, italic=True)
        rule_line(s, cx + 0.15, y + 0.74, cwr - 0.30, LINE)
        bullets(s, cx + 0.15, y + 0.84, cwr - 0.30, 2.34, items, size=8, gap=4.5)

    by = 5.10
    card(s, LM, by, CW, 1.72, fill=PALE2, line=LINE)
    box(s, LM, by, 0.075, 1.72, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    text(s, LM + 0.24, by + 0.15, CW - 0.48, 0.22,
         [[("OUR OWN MEASUREMENTS   ", {"bold": True, "color": NAVY,
                                        "spacing": 1.0}),
           ("every figure on this deck reproduces from the repository, with the "
            "script that produced it", {"italic": True, "color": MUTED})]],
         size=9)
    rule_line(s, LM + 0.24, by + 0.44, CW - 0.48, STEEL)

    evidence = [
        ["docs/LEGAL_SOURCE_MATRIX.md \u2014 24 primary sources audited clause by "
         "clause; rules withdrawn or corrected where the sources did not bear "
         "them out",
         "docs/OCR_CAPABILITY.md \u2014 105 of 109 printed values recovered across "
         "24 degraded labels",
         "out/panel-accuracy/results.json \u2014 the bracketed panel area "
         "contained the truth in 8 of 8"],
        ["out/real-label-evaluation-rescan-final/REPORT.md \u2014 12/16 accepted "
         "exactly on 10 real photographs, 0 wrong",
         "data/evaluation/ingredient-panels/ \u2014 7 Wikimedia CC-licensed "
         "panels; 0 false positives, both traps clean",
         "out/hindi-allergen-measurement/results.json \u2014 Devanagari screening "
         "through real recognition, end to end"],
    ]
    ew = (CW - 0.48 - 0.30) / 2
    for i, group in enumerate(evidence):
        ex = LM + 0.24 + i * (ew + 0.30)
        bullets(s, ex, by + 0.56, ew, 1.05, group, size=8, gap=4)

    text(s, LM + 0.24, by + 1.42, CW - 0.48, 0.22,
         "Evaluation photographs are Wikimedia Commons CC BY / CC BY-SA; "
         "attribution is retained in sources.json and travels with the images. "
         "These are unblinded development sets, not independent accuracy "
         "estimates \u2014 stated as such wherever they are quoted.",
         size=8, color=MUTED, italic=True, line=1.1)


def main():
    prs = Presentation(str(TEMPLATE))
    delete_slide(prs, 6)          # the template's own instructions page
    slide1(prs)
    slide2(prs)
    slide3(prs)
    slide4(prs)
    slide5(prs)
    slide6(prs)
    assert len(prs.slides) == 6, len(prs.slides)
    prs.save(str(OUT))
    print("wrote", OUT)
    print("slides:", len(prs.slides))


if __name__ == "__main__":
    main()
