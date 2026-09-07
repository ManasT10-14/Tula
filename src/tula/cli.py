"""Command line entry point.

    python -m tula.cli data/samples/biscuit-front.jpg

One command, one photo, one report -- the vertical slice the whole build is
sequenced around.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .analyse import AnalyseOptions, Capture, analyse
from .domain.enums import Lane, Panel, Verdict
from .report import docx_export, pdf, render

_TICK = {
    Verdict.PASS: "ok  ",
    Verdict.VIOLATION: "FAIL",
    Verdict.ADVISORY: "NOTE",
    Verdict.INCONCLUSIVE: "??  ",
    Verdict.UNVERIFIED: "??  ",
    Verdict.EXEMPT: "ex  ",
    Verdict.NOT_APPLICABLE: "--  ",
}


def _capture(spec: str) -> Capture:
    """`path` or `panel=path`, e.g. `bottom=carton-base.jpg`."""
    if "=" in spec:
        panel, _, path = spec.partition("=")
        return Capture(path=path, panel=Panel(panel.strip().lower()))
    return Capture(path=spec, panel=Panel.PDP)


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page, which mangles the
    # rupee sign, the plus-minus and any Devanagari the label carries.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(
        prog="tula",
        description="Check a packaged commodity against the Legal Metrology "
                    "(Packaged Commodities) Rules, 2011.",
    )
    parser.add_argument("images", nargs="+", metavar="[PANEL=]IMAGE",
                        help="captured faces, e.g. pdp=front.jpg bottom=base.jpg")
    parser.add_argument("-o", "--out", default="out", help="output directory")
    parser.add_argument("--engine", default=None, choices=["auto", "rapidocr", "fixture"],
                        help="ocr engine: rapidocr | fixture | auto (default auto)")
    parser.add_argument("--rules", default=None, help="rule pack directory")
    parser.add_argument("--officer", default=None, help="inspecting officer id")
    parser.add_argument("--lane", default="field",
                        choices=[l.value for l in Lane])
    parser.add_argument("--packing-date", default=None,
                        help="ISO date; overrides the declared date and selects "
                             "the rule version in force")
    parser.add_argument("--complete", action="store_true",
                        help="attest that every printed face of the package is in "
                             "this capture. Without it, a declaration that is not "
                             "found is reported inconclusive rather than missing.")
    parser.add_argument("--premises", default="", help="premises for the draft notice")
    parser.add_argument("--no-report", action="store_true",
                        help="print findings only, write nothing")
    args = parser.parse_args(argv)

    try:
        captures = [_capture(spec) for spec in args.images]
        packing_date = date.fromisoformat(args.packing_date) if args.packing_date else None
    except ValueError as exc:
        parser.error(str(exc))
    missing = [c.path for c in captures
               if not Path(c.path).exists() and not Path(c.path).with_suffix(".txt").exists()]
    if missing:
        print(f"error: no such capture: {', '.join(missing)}", file=sys.stderr)
        return 2

    options = AnalyseOptions(
        lane=Lane(args.lane),
        operator=args.officer,
        packing_date=packing_date,
        engine_name=args.engine,
        rules_dir=args.rules,
        capture_is_complete=args.complete,
    )
    analysis = analyse(captures, options)

    # ---- terminal summary ------------------------------------------------
    print()
    print(f"  {render.headline(analysis)}")
    print(f"  reference {analysis.scan.scan_id} · rules {analysis.rules_version} "
          f"· evidence Tier {analysis.scan.tier.value} · {analysis.elapsed_ms} ms "
          f"· engine {analysis.engine}")
    print()

    for finding in analysis.findings:
        print(f"  [{_TICK[finding.verdict]}] {finding.citation.clause:<34} {finding.message}")
        if finding.measured and finding.threshold is not None:
            print(f"         {finding.measured.render()}  against  "
                  f"{finding.threshold:.2f} mm minimum")
        if finding.verdict in (Verdict.VIOLATION, Verdict.INCONCLUSIVE) and finding.detail:
            print(f"         {finding.detail}")

    if analysis.warnings:
        print()
        for warning in analysis.warnings:
            print(f"  note: {warning}")

    if args.no_report:
        return 1 if analysis.violations else 0

    # ---- artefacts -------------------------------------------------------
    out = Path(args.out)
    stem = f"{analysis.scan.scan_id}"
    pdf_path = pdf.write(analysis, out / f"{stem}-report.pdf")
    docx_path = docx_export.write(analysis, out / f"{stem}-report.docx")
    notice_path = docx_export.write_notice(
        analysis, out / f"{stem}-notice.docx", premises=args.premises
    )

    print()
    print("  written:")
    for label, path in (
        ("report (pdf)", pdf_path),
        ("record (json)", pdf_path.with_suffix(".json")),
        ("report (docx)", docx_path),
        ("draft notice", notice_path),
    ):
        print(f"    {label:<14} {path}")
    print()

    return 1 if analysis.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
