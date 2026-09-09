"""PDF compliance report.

Two things a generic PDF dump would get wrong and this does not:

  * Devanagari. A report on an Indian label that cannot print the label's own
    Hindi text is not fit for purpose, so a Unicode face is registered where
    one exists and the text is only transliterated away as a last resort.
  * The machine-readable record travels with the document. The JSON is written
    beside the PDF and referenced from it, which is the practical half of the
    PDF/A-3 embedded-attachment story in PRD section 13.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    Image as PdfImage,
)
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..domain.enums import Verdict
from ..domain.models import Analysis
from . import render
from .evidence import crops, originals

# Faces that ship with Windows and cover Devanagari plus the rupee sign.
_UNICODE_CANDIDATES = [
    ("NirmalaUI", r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\NirmalaB.ttf"),
    ("Mangal", r"C:\Windows\Fonts\mangal.ttf", r"C:\Windows\Fonts\mangalb.ttf"),
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

_ACCENT = colors.HexColor("#0A6669")
_BRASS = colors.HexColor("#856019")
_INK = colors.HexColor("#0C1716")
_MUTED = colors.HexColor("#56635F")
_LINE = colors.HexColor("#D5DEDB")
_SUNK = colors.HexColor("#F0F3F2")


def _register_fonts() -> tuple[str, str]:
    """Register a Unicode face if one is available; report what we got."""
    for name, regular, bold in _UNICODE_CANDIDATES:
        if not Path(regular).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, regular))
            bold_name = name
            if Path(bold).exists():
                bold_name = f"{name}-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, bold))
            return name, bold_name
        except (OSError, TTFError):
            pass  # Try the next installed font; ASCII fallback is explicit.
    return "Helvetica", "Helvetica-Bold"


_ASCII_FALLBACK = {"\u20b9": "Rs.", "\u00b1": "+/-", "\u2013": "-", "\u2014": "-",
                   "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}


def _safe(text: str, unicode_ok: bool) -> str:
    text = str(text)
    if not unicode_ok:
        for bad, good in _ASCII_FALLBACK.items():
            text = text.replace(bad, good)
        text = text.encode("latin-1", "replace").decode("latin-1")
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def write(analysis: Analysis, path: str | Path, *, json_sidecar: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    body_font, bold_font = _register_fonts()
    unicode_ok = body_font != "Helvetica"

    base = getSampleStyleSheet()
    style_body = ParagraphStyle(
        "body", parent=base["BodyText"], fontName=body_font, fontSize=8.6,
        leading=12.2, textColor=_INK, alignment=TA_LEFT, spaceAfter=0,
    )
    style_small = ParagraphStyle(
        "small", parent=style_body, fontSize=7.6, leading=10.4, textColor=_MUTED,
        allowWidows=0, allowOrphans=0,
    )
    style_h1 = ParagraphStyle(
        "h1", parent=base["Title"], fontName=bold_font, fontSize=21, leading=23,
        textColor=_INK, alignment=TA_LEFT, spaceAfter=2,
    )
    style_h2 = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName=bold_font, fontSize=11.5,
        leading=14, textColor=_INK, spaceBefore=14, spaceAfter=5, keepWithNext=True,
    )
    style_eyebrow = ParagraphStyle(
        "eyebrow", parent=style_small, fontName=bold_font, fontSize=7.4,
        textColor=_BRASS, spaceAfter=1,
    )

    story = []

    # ---- masthead --------------------------------------------------------
    story.append(Paragraph("LEGAL METROLOGY (PACKAGED COMMODITIES) RULES, 2011", style_eyebrow))
    story.append(Paragraph("Compliance Inspection Report", style_h1))
    story.append(Paragraph(_safe(render.review_banner(analysis), unicode_ok), style_eyebrow))
    story.append(
        Paragraph(
            _safe(render.headline(analysis), unicode_ok),
            ParagraphStyle("lede", parent=style_body, fontSize=10, leading=13.5,
                           textColor=_INK, spaceBefore=3),
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            _safe(
                f"Reference {analysis.scan.scan_id}  ·  Rules version "
                f"{analysis.rules_version}  ·  Evidence Tier {analysis.scan.tier.value}"
                f"  ·  Revision {analysis.review.revision}  ·  Generated by TATVA",
                unicode_ok,
            ),
            style_small,
        )
    )

    story.append(Spacer(1, 8))
    story.append(Paragraph(_safe(
        f"Machine potential violations: {len(analysis.violations)} | "
        f"Inspector verified: {len(analysis.verified_violations)} | "
        f"Awaiting resolution: {len(analysis.pending_review)}", unicode_ok), style_body))

    # ---- sections --------------------------------------------------------
    for section in render.build(analysis):
        heading = Paragraph(_safe(section.title, unicode_ok), style_h2)
        if section.kind == "kv" and len(section.rows) <= 8:
            block = [heading, _kv_table(section.rows, body_font, bold_font, unicode_ok, style_body)]
            if section.note:
                block.extend([Spacer(1, 4), Paragraph(_safe(section.note, unicode_ok), style_small)])
            story.append(KeepTogether(block))
            continue
        if section.kind == "table" and not section.rows:
            block = [heading, Paragraph("Nothing recorded.", style_small)]
            if section.note:
                block.extend([Spacer(1, 4), Paragraph(_safe(section.note, unicode_ok), style_small)])
            story.append(KeepTogether(block))
            continue
        story.append(heading)

        if section.kind == "kv":
            story.append(_kv_table(section.rows, body_font, bold_font, unicode_ok, style_body))
        elif section.kind == "table":
            story.append(
                _grid_table(section.columns, section.rows, body_font, bold_font,
                            unicode_ok, style_small)
            )
        elif section.kind == "text":
            for line in section.rows:
                story.append(Paragraph("— " + _safe(line, unicode_ok), style_small))
                story.append(Spacer(1, 2))
        elif section.kind == "findings":
            story.extend(
                _findings_blocks(section.rows, body_font, bold_font, unicode_ok,
                                 style_body, style_small, analysis)
            )

        if section.note:
            story.append(Spacer(1, 4))
            story.append(Paragraph(_safe(section.note, unicode_ok), style_small))

    full_images = list(originals(analysis))
    if full_images:
        for index, (name, frame, stream, size) in enumerate(full_images):
            factor = min(165 * mm / size[0], 110 * mm / size[1], 1)
            block = [
                Spacer(1, 8), Paragraph(_safe(name + " | " + Path(frame).name, unicode_ok), style_small),
                Spacer(1, 4), PdfImage(stream, width=size[0]*factor, height=size[1]*factor),
            ]
            if index == 0:
                block[:0] = [Paragraph("Original image appendix", style_h2), Paragraph(
                    "Preserved images shown for context. Evidence hashes identify the original files; report illustrations may be reduced and oriented for reading.",
                    style_small)]
            story.append(KeepTogether(block))
    crop_rows = list(crops(analysis))
    if crop_rows:
        for index, (name, frame, box, stream, size) in enumerate(crop_rows):
            factor = min(160 * mm / size[0], 24 * mm / size[1], 1)
            block = [
                Paragraph(_safe(f"{name} | {Path(frame).name} | pixels {box}", unicode_ok), style_small),
                Spacer(1, 4), PdfImage(stream, width=size[0]*factor, height=size[1]*factor), Spacer(1, 10),
            ]
            if index == 0:
                block[:0] = [Paragraph("Evidence crops", style_h2), Paragraph(
                    "Highlighted rectangles identify the source regions. Coordinates use the recorded capture image, before report scaling.",
                    style_small)]
            story.append(KeepTogether(block))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Compliance report {analysis.scan.scan_id} revision {analysis.review.revision}",
        author="TATVA", subject="Legal Metrology (Packaged Commodities) Rules, 2011",
    )
    def footer(canvas, document):
        _footer(canvas, document, analysis)
    doc.build(story, onLaterPages=footer, onFirstPage=footer)
    # Portable machine-readable attachment. This is an ordinary PDF, not a
    # claim of PDF/A-3 conformance (which needs separate archival validation).
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter(clone_from=PdfReader(BytesIO(path.read_bytes())))
    writer.add_attachment("analysis.json", analysis.model_dump_json(indent=2).encode("utf-8"))
    with path.open("wb") as fh:
        writer.write(fh)

    if json_sidecar:
        sidecar = path.with_suffix(".json")
        sidecar.write_text(
            json.dumps(analysis.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return path


def _footer(canvas, doc, analysis=None):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_MUTED)
    label = "TATVA | Inspection report"
    if analysis:
        label += f" | {analysis.scan.scan_id} | Revision {analysis.review.revision}"
    canvas.drawString(18 * mm, 10 * mm, label)
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _summary_strip(counts, body_font, bold_font, unicode_ok, style_small):
    order = [Verdict.VIOLATION, Verdict.ADVISORY, Verdict.INCONCLUSIVE, Verdict.UNVERIFIED,
             Verdict.EXEMPT, Verdict.PASS]
    cells, styles = [], [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, _LINE),
        ("BACKGROUND", (0, 0), (-1, -1), _SUNK),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for index, verdict in enumerate(order):
        count = counts.get(verdict.value, 0)
        label = render.VERDICT_LABEL[verdict]
        rgb = render.VERDICT_RGB[verdict]
        colour = colors.Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        cells.append(
            Paragraph(
                f'<font name="{bold_font}" size="13" color="#{colour.hexval()[2:]}">{count}</font>'
                f'<br/><font size="7">{label.upper()}</font>',
                style_small,
            )
        )
        if count == 0:
            styles.append(("TEXTCOLOR", (index, 0), (index, 0), _MUTED))
    table = Table([cells], colWidths=[174 * mm / len(order)] * len(order))
    table.setStyle(TableStyle(styles))
    return table


def _kv_table(rows, body_font, bold_font, unicode_ok, style_body):
    data = [
        [
            Paragraph(f'<font name="{bold_font}">{_safe(k, unicode_ok)}</font>', style_body),
            Paragraph(_safe(v, unicode_ok), style_body),
        ]
        for k, v in rows
    ]
    table = Table(data, colWidths=[52 * mm, 122 * mm], splitInRow=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _grid_table(columns, rows, body_font, bold_font, unicode_ok, style_small):
    if not rows:
        return Paragraph("Nothing recorded.", style_small)
    width = 174.0
    widths = [width / len(columns)] * len(columns)
    if columns == ["Declaration", "As printed", "Panel", "Scripts"]:
        widths = [42, 90, 20, 22]
    elif len(columns) == 3 and columns[0] == "Field":
        widths = [33, 80, 61]
    elif len(columns) == 3 and columns[0] == "Frame":
        widths = [38, 108, 28]
    elif columns[0] == "Observation":
        widths = [32, 62, 80]
    data = [
        [
            Paragraph(f'<font name="{bold_font}">{_safe(c, unicode_ok)}</font>', style_small)
            for c in columns
        ]
    ]
    for row in rows:
        data.append([Paragraph(_safe(cell, unicode_ok), style_small) for cell in row])
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1, splitInRow=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), _SUNK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, _LINE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _findings_blocks(findings, body_font, bold_font, unicode_ok, style_body, style_small, analysis=None):
    blocks = []
    for index, finding in enumerate(findings, start=1):
        rgb = render.VERDICT_RGB[finding.verdict]
        colour = colors.Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
        header = Table(
            [
                [
                    Paragraph(
                        f'<font name="{bold_font}" size="7.4" color="#{colour.hexval()[2:]}">'
                        f'{render.VERDICT_LABEL[finding.verdict].upper()}</font>',
                        style_small,
                    ),
                    Paragraph(
                        f'<font name="{bold_font}" size="9.4">{index:02d}. '
                        f"{_safe(render.finding_message(finding), unicode_ok)}</font>",
                        style_body,
                    ),
                ]
            ],
            colWidths=[26 * mm, 148 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        detail = _kv_table(render.finding_rows(finding, analysis), body_font, bold_font,
                           unicode_ok, style_small)
        # The heading stays with the first row; large officer explanations can
        # continue across pages without pushing a whole finding off the page.
        # A standalone Spacer would consume a section heading's keepWithNext,
        # allowing "Findings" to be stranded at the bottom of the previous page.
        header.keepWithNext = True
        header.spaceBefore = 8
        blocks.extend([header, detail])
    return blocks
