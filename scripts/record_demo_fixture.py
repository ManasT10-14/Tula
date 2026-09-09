"""Record a real analysis of a set of photographs, for replay in a demonstration.

Run: python scripts/record_demo_fixture.py --name nakoda-ratlami-sev images/*.jpeg

This runs the ordinary pipeline -- the same code path an upload takes, with
replay switched off -- and stores its output under `data/demo-fixtures/`, keyed
by the SHA-256 of every image in the set. A later upload of those exact files
returns the stored result immediately instead of spending forty seconds
recognising them again in front of an audience.

The recording is not an edited or idealised result. Whatever the pipeline
decided is what gets stored, including its inconclusive findings, and if the
rule pack or the recogniser changes the fixture stops being used until it is
recorded again. Re-record with the same `--name` to replace one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Never replay while recording: that would store a copy of an older recording.
os.environ["TULA_DEMO_FIXTURES"] = "off"

from tula.analyse import AnalyseOptions, Capture, analyse  # noqa: E402
from tula.demo import fixtures  # noqa: E402
from tula.domain.enums import Lane, Panel  # noqa: E402
from tula.rules.engine import RulesEngine  # noqa: E402


def panel_for(path: Path, explicit: dict[str, str]) -> Panel:
    named = explicit.get(path.name)
    return Panel(named) if named else Panel.BACK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--name", required=True,
                        help="Short identifier shown on the replayed inspection.")
    parser.add_argument("--panels", default="",
                        help="Optional 'file.jpg=pdp,other.jpg=back' panel assignment.")
    parser.add_argument("--lane", default="field")
    parser.add_argument("--engine", default="rapidocr")
    parser.add_argument("--complete", action="store_true",
                        help="Attest that every printed face is in this set.")
    args = parser.parse_args()

    images = [p for p in args.images if p.is_file()]
    missing = [str(p) for p in args.images if not p.is_file()]
    if missing:
        print("no such file:", ", ".join(missing), file=sys.stderr)
        return 2
    if not images:
        print("no images given", file=sys.stderr)
        return 2

    explicit = dict(
        pair.split("=", 1) for pair in args.panels.split(",") if "=" in pair
    )
    captures = [Capture(str(p), panel_for(p, explicit)) for p in images]

    rules = RulesEngine.from_directory()
    print(f"analysing {len(captures)} image(s) live under pack {rules.pack.version} ...")
    analysis = analyse(captures, AnalyseOptions(
        lane=Lane(args.lane), engine_name=args.engine,
        capture_is_complete=args.complete,
    ), rules=rules)
    if analysis.demo_fixture:
        print("refusing to record a replay; replay was not disabled", file=sys.stderr)
        return 1

    counts = analysis.counts()
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items() if v))

    target = fixtures.directory()
    target.mkdir(parents=True, exist_ok=True)
    record_name = f"{args.name}.json"
    (target / record_name).write_text(json.dumps({
        "name": args.name,
        "recorded": datetime.now(UTC).date().isoformat(),
        "rules_version": analysis.rules_version,
        "engine": analysis.engine,
        "source_images": [p.name for p in images],
        "analysis": json.loads(analysis.model_dump_json()),
    # Compact: this is a machine-written record, re-produced by re-running the
    # script rather than edited, and indenting 660 KB of OCR diagnostics costs
    # a megabyte in the repository for readability nobody can use.
    }, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    manifest_path = target / fixtures.MANIFEST
    manifest = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    key = fixtures.key_for([str(p) for p in images])
    # Drop any previous entry pointing at this record, so re-recording with a
    # different image set does not leave the old key dangling.
    manifest = {k: v for k, v in manifest.items() if v.get("name") != args.name}
    manifest[key] = {"name": args.name, "record": record_name,
                     "images": [p.name for p in images]}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    print(f"recorded -> {target / record_name}")
    print(f"key       {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
