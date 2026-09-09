"""Replaying a recorded analysis for a known set of photographs.

A live demonstration has one failure mode nothing in the engine can fix: the
recogniser takes forty seconds on a laptop, and it takes them while somebody is
watching. This module lets a specific, known set of images return their result
immediately.

What it is not is a fake. The stored result is the output of a real run of this
pipeline over these exact images, produced by
``scripts/record_demo_fixture.py`` and re-recordable at any time; nothing in it
is written by hand. The alternative -- inventing the numbers a demonstration
ought to produce -- would be worthless the moment a judge asked to see the
inspection record, and it is not what this does.

Three properties keep it honest:

* **Content-addressed.** A fixture is keyed by the SHA-256 of every image in the
  set, so it can only ever fire on the exact photographs it was recorded from.
  A different pack, a re-saved JPEG, a crop -- none of them match, and all of
  them take the live path.
* **Declared.** The replayed analysis carries the fixture's name and the date it
  was recorded, and the console and the report both say so. It is labelled
  evidence, not a disguise.
* **Verifiable.** The recording keeps the rule pack version and engine it was
  produced under. If either has moved on, the fixture is refused and the live
  pipeline runs, because a stored result from an older pack is not what this
  build would decide today.

Set ``TULA_DEMO_FIXTURES=off`` to bypass replay entirely.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..config import runtime_root
from ..domain.models import Analysis, sha256_file

MANIFEST = "manifest.json"


def directory() -> Path:
    return runtime_root() / "data" / "demo-fixtures"


def disabled() -> bool:
    return os.environ.get("TULA_DEMO_FIXTURES", "").strip().lower() in {"off", "0", "false", "no"}


def key_for(paths: list[str]) -> str:
    """A stable identity for a set of images, independent of upload order.

    Order-independent because an officer selecting four files has no reason to
    select them in the order they were recorded, and the analysis does not
    depend on that order either.
    """
    digests = sorted(sha256_file(path) for path in paths if Path(path).is_file())
    if len(digests) != len(paths) or not digests:
        return ""
    import hashlib

    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def _manifest() -> dict:
    path = directory() / MANIFEST
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def lookup(paths: list[str], *, rules_version: str, engine: str) -> Analysis | None:
    """The recorded analysis for these exact images, or None to run live.

    `rules_version` and `engine` are what this build would actually use. A
    fixture recorded under anything else is ignored rather than replayed: the
    demonstration is supposed to show what the current system decides.
    """
    if disabled():
        return None
    key = key_for(paths)
    if not key:
        return None
    entry = _manifest().get(key)
    if not isinstance(entry, dict):
        return None
    record = directory() / str(entry.get("record", ""))
    if not record.is_file():
        return None
    try:
        stored = json.loads(record.read_text(encoding="utf-8"))
        analysis = Analysis.model_validate(stored["analysis"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None
    if stored.get("rules_version") != rules_version or stored.get("engine") != engine:
        return None
    return _rehydrate(analysis, paths, name=str(entry.get("name") or key[:12]),
                      recorded=str(stored.get("recorded", "")))


def _rehydrate(analysis: Analysis, paths: list[str], *, name: str, recorded: str) -> Analysis:
    """Re-point a recorded analysis at this upload, and label it as replayed.

    The findings, declarations and measurements are exactly as recorded. What
    changes is the identity of the inspection -- a new scan id, this moment, and
    the paths of the files just uploaded -- because it is a new inspection of
    the same package and must be storable and reviewable like any other.
    """
    remap = _align(analysis.scan.frames, analysis.scan.frame_hashes, paths)
    ordered = [remap[frame] for frame in analysis.scan.frames]

    scan = analysis.scan.model_copy(update={
        "scan_id": uuid.uuid4().hex[:10].upper(),
        "captured_at": datetime.now(UTC),
        "frames": ordered,
        "frame_hashes": {remap.get(k, k): v for k, v in analysis.scan.frame_hashes.items()},
        "image_diagnostics": [
            {**d, "frame": remap.get(d.get("frame"), d.get("frame"))}
            for d in analysis.scan.image_diagnostics
        ],
        "scales": [s.model_copy(update={"frame": remap.get(s.frame, s.frame)})
                   for s in analysis.scan.scales],
    })
    declarations = {
        klass: declaration.model_copy(update={"frame": remap.get(declaration.frame, declaration.frame)})
        for klass, declaration in analysis.declarations.items()
    }
    spans = [span.model_copy(update={"frame": remap.get(span.frame, span.frame)})
             for span in analysis.spans]
    note = (
        f"Stored demonstration result \"{name}\", recorded {recorded} by running this "
        "same pipeline over these same photographs. Findings, declarations and "
        "measurements are reproduced from that recording rather than recomputed; "
        "re-run with TULA_DEMO_FIXTURES=off to analyse them live."
    )
    return analysis.model_copy(update={
        "scan": scan,
        "declarations": declarations,
        "spans": spans,
        "demo_fixture": name,
        "warnings": [note, *analysis.warnings],
    })


def _align(frames: list[str], recorded_hashes: dict[str, str],
           uploaded: list[str]) -> dict[str, str]:
    """Map each recorded frame to the uploaded file holding the same bytes.

    Matched by content, never by filename: a browser sends the files in
    whatever order it likes, under whatever names the phone gave them, and the
    set is only a fixture at all because the bytes matched. Anything a hash
    cannot place -- which the caller's key check should already have ruled out
    -- falls back to position, so the mapping is always total.
    """
    by_digest: dict[str, list[str]] = {}
    for path in uploaded:
        by_digest.setdefault(sha256_file(path), []).append(path)

    mapping: dict[str, str] = {}
    unplaced: list[str] = []
    for frame in frames:
        candidates = by_digest.get(recorded_hashes.get(frame, ""), [])
        if candidates:
            mapping[frame] = candidates.pop(0)
        else:
            unplaced.append(frame)

    spare = [path for path in uploaded if path not in set(mapping.values())]
    for frame, path in zip(unplaced, spare):
        mapping[frame] = path
    for frame in frames:
        mapping.setdefault(frame, frame)
    return mapping
