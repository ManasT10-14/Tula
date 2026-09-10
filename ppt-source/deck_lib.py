"""Shared drawing helpers for the SIH deck build.

Everything is a native PowerPoint vector shape: sharp at any zoom, editable by
the team afterwards, and guaranteed to sit exactly where the grid puts it.
"""
from __future__ import annotations

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ---------------------------------------------------------------- palette ---
# Derived from the template's own theme: dk2 #1F497D is the title colour and
# accent1 #4F81BD the accent. Everything else is built out from those two so
# the deck reads as one document with the SIH shell, not as a graft.
NAVY = RGBColor(0x1F, 0x49, 0x7D)    # template dk2 - primary
DEEP = RGBColor(0x13, 0x2E, 0x4D)    # darker navy for headings
BLUE = RGBColor(0x4F, 0x81, 0xBD)    # template accent1
STEEL = RGBColor(0x7C, 0x9C, 0xC4)
PALE = RGBColor(0xEE, 0xF3, 0xF9)    # card fill
PALE2 = RGBColor(0xDD, 0xE7, 0xF3)   # stronger fill
LINE = RGBColor(0xC3, 0xD2, 0xE3)    # borders
INK = RGBColor(0x1B, 0x2A, 0x39)     # body text
MUTED = RGBColor(0x62, 0x73, 0x84)   # secondary text
FAINT = RGBColor(0x8B, 0x99, 0xA8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
# semantic - used only where the meaning is real, never as decoration
RED = RGBColor(0xA3, 0x37, 0x2B)     # violation
AMBER = RGBColor(0xA9, 0x68, 0x0E)   # advisory / open item
GREEN = RGBColor(0x41, 0x70, 0x26)   # pass
GREY = RGBColor(0x7E, 0x8C, 0x9A)    # not applicable / exempt / unverified

BODY = "Arial"             # template minor font
HEAD = "Times New Roman"   # template major/title font
DEVA = "Nirmala UI"        # ships with Windows; embeds on PDF export

# ------------------------------------------------------------------ grid ---
LM = 0.55
RM = 0.55
CW = 13.3333 - LM - RM     # 12.2333 content width
BOT = 6.86                 # last usable line, clear of the footer bar at 6.95


def _spacing(run, points):
    """Letter-spacing, which python-pptx does not expose."""
    run.font._rPr.set("spc", str(int(points * 100)))


def box(slide, x, y, w, h, *, fill=None, line=None, lw=0.75, radius=None,
        shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    """A rectangle. radius is a fraction of half the shorter side."""
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(lw)
    s.shadow.inherit = False
    s.text_frame.word_wrap = True
    return s


def text(slide, x, y, w, h, runs, *, size=11, color=INK, bold=False, font=BODY,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=None, line=0.92,
         space_after=0, italic=False):
    """A text box. `runs` is a string, or a list of paragraphs.

    A paragraph is a string, a dict of per-paragraph overrides, or a list of
    (text, override-dict) tuples for mixed formatting inside one line.
    """
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        over_para = {}
        if isinstance(para, dict):
            over_para = para
            pieces = [(para.get("t", ""), para)]
        elif isinstance(para, list):
            pieces = para
        else:
            pieces = [para]
        for piece in pieces:
            if isinstance(piece, tuple):
                s, over = piece
            elif isinstance(piece, dict):
                s, over = piece.get("t", ""), piece
            else:
                s, over = piece, {}
            r = p.add_run()
            r.text = s
            f = r.font
            f.size = Pt(over.get("size", over_para.get("size", size)))
            f.bold = over.get("bold", over_para.get("bold", bold))
            f.italic = over.get("italic", over_para.get("italic", italic))
            f.name = over.get("font", over_para.get("font", font))
            f.color.rgb = over.get("color", over_para.get("color", color))
            sp = over.get("spacing", over_para.get("spacing", spacing))
            if sp:
                _spacing(r, sp)
        p.line_spacing = over_para.get("line", line)
        p.space_after = Pt(over_para.get("space_after", space_after))
        p.space_before = Pt(over_para.get("space_before", 0))
    return tb


def rule_line(slide, x, y, w, color=LINE, h=0.014):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                               Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    return s


def eyebrow(slide, x, y, label, *, w=6.0, color=NAVY, size=9.5, rule=True,
            rule_color=None):
    """A small-caps section label over a hairline - the deck's one structural
    device. Used only where the label names a required template pointer."""
    tb = text(slide, x, y, w, 0.20, label.upper(), size=size, bold=True,
              color=color, spacing=1.3)
    if rule:
        rule_line(slide, x, y + 0.235, w, rule_color or LINE, 0.018)
    return tb


def chevron(slide, cx, cy, size=0.19, color=STEEL):
    """A small right-pointing triangle, centred on (cx, cy)."""
    s = slide.shapes.add_shape(MSO_SHAPE.ISOSCELES_TRIANGLE,
                               Inches(cx - size / 2), Inches(cy - size / 2),
                               Inches(size), Inches(size))
    s.rotation = 90
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    return s


def delete_shape(shape):
    shape._element.getparent().remove(shape._element)


def delete_slide(prs, index):
    xml_slides = prs.slides._sldIdLst
    slides = list(xml_slides)
    rId = slides[index].get(qn("r:id"))
    prs.part.drop_rel(rId)
    xml_slides.remove(slides[index])


def set_title(slide, string, *, size=32, devanagari=None):
    """Rewrite the template's title placeholder, keeping its identity."""
    for sh in slide.shapes:
        if sh.is_placeholder and sh.name.startswith("Title"):
            tf = sh.text_frame
            tf.clear()
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = string
            r.font.size = Pt(size)
            r.font.bold = True
            r.font.name = HEAD
            r.font.color.rgb = NAVY
            if devanagari:
                r2 = p.add_run()
                r2.text = devanagari
                r2.font.size = Pt(size - 4)
                r2.font.bold = True
                r2.font.name = DEVA
                r2.font.color.rgb = BLUE
            # keep it clear of the team badge (left) and the SIH logo (right)
            sh.left, sh.top = Inches(1.95), Inches(0.10)
            sh.width, sh.height = Inches(8.55), Inches(0.95)
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            return sh
    return None


def set_team_badge(slide, name):
    for sh in slide.shapes:
        if sh.name.startswith("Oval"):
            sh.line.color.rgb = STEEL
            sh.line.width = Pt(1.0)
            tf = sh.text_frame
            tf.clear()
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            p.line_spacing = 0.9
            r = p.add_run()
            r.text = name
            r.font.size = Pt(8.5)
            r.font.bold = True
            r.font.name = BODY
            r.font.color.rgb = NAVY
            return sh
    return None


def strip_body(slide):
    """Remove the template's instructional body box; its pointers are re-laid
    out as eyebrow labels so the required headings all survive."""
    for sh in list(slide.shapes):
        if sh.name == "TextBox 8":
            delete_shape(sh)


def card(slide, x, y, w, h, *, fill=PALE, line=LINE, accent=None, accent_h=0.055):
    """A panel with an optional accent bar across its top edge."""
    s = box(slide, x, y, w, h, fill=fill, line=line, radius=0.055)
    if accent:
        bar = box(slide, x, y, w, accent_h, fill=accent, radius=0.5,
                  shape=MSO_SHAPE.RECTANGLE)
        bar.line.fill.background()
    return s


# --------------------------------------------------------------- flowchart ---

def pill(slide, x, y, w, h, s, *, fill=WHITE, line=LINE, color=INK, size=7.5,
         bold=True, lw=0.75):
    """A small capsule chip - the unit the architecture rows are built from."""
    box(slide, x, y, w, h, fill=fill, line=line, lw=lw, radius=0.5)
    text(slide, x + 0.05, y, w - 0.10, h, s, size=size, bold=bold, color=color,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line=0.95)


def down_arrow(slide, cx, y, h, *, color=STEEL, head=0.105, stem=0.022):
    """A stem with a solid head, pointing down from (cx, y) over height h."""
    body = h - head
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(cx - stem / 2),
                               Inches(y), Inches(stem), Inches(body))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    t = slide.shapes.add_shape(MSO_SHAPE.ISOSCELES_TRIANGLE,
                               Inches(cx - head / 2), Inches(y + body),
                               Inches(head), Inches(head))
    t.rotation = 180
    t.fill.solid()
    t.fill.fore_color.rgb = color
    t.line.fill.background()
    t.shadow.inherit = False


def badge(slide, cx, cy, n, *, d=0.20, fill=NAVY, color=WHITE, size=8):
    """A numbered disc marking a step in a flow."""
    box(slide, cx - d / 2, cy - d / 2, d, d, fill=fill, shape=MSO_SHAPE.OVAL)
    text(slide, cx - d / 2, cy - d / 2, d, d, str(n), size=size, bold=True,
         color=color, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def bullets(slide, x, y, w, h, items, *, size=8, color=INK, gap=3.5, line=1.1,
            mark="\u2022  "):
    return text(slide, x, y, w, h,
                [{"t": mark + it, "space_after": gap} for it in items],
                size=size, color=color, line=line)
