"""A recorded analysis may be replayed, and must never be mistaken for a live one.

The point of these is not that replay is fast. It is that replay is *narrow*: it
fires on exactly the images it was recorded from and on nothing else, it refuses
to speak for a rule pack it was not recorded under, and it says on its face that
it is a recording.
"""
from __future__ import annotations

import json
import shutil

import pytest
from PIL import Image

from tula.demo import fixtures
from tula.domain.models import Analysis, PackageFacts, Scan, sha256_file


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A private fixture directory and three distinguishable images."""
    monkeypatch.setattr(fixtures, "directory", lambda: tmp_path / "demo-fixtures")
    monkeypatch.delenv("TULA_DEMO_FIXTURES", raising=False)
    images = []
    for index, shade in enumerate(("red", "green", "blue")):
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (40, 40), shade).save(path)
        images.append(str(path))
    return tmp_path, images


def record(tmp_path, images, *, name="qa", rules_version="pack-1", engine="rapidocr"):
    analysis = Analysis(
        scan=Scan(scan_id="RECORDED", frames=list(images),
                  frame_hashes={p: sha256_file(p) for p in images}),
        package=PackageFacts(), rules_version=rules_version, engine=engine,
    )
    target = tmp_path / "demo-fixtures"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{name}.json").write_text(json.dumps({
        "name": name, "recorded": "2026-09-10",
        "rules_version": rules_version, "engine": engine,
        "analysis": json.loads(analysis.model_dump_json()),
    }), encoding="utf-8")
    (target / fixtures.MANIFEST).write_text(json.dumps({
        fixtures.key_for(images): {"name": name, "record": f"{name}.json"},
    }), encoding="utf-8")
    return analysis


def test_the_recorded_set_replays_and_says_that_it_did(workspace):
    tmp_path, images = workspace
    record(tmp_path, images)
    replayed = fixtures.lookup(images, rules_version="pack-1", engine="rapidocr")
    assert replayed is not None
    assert replayed.demo_fixture == "qa"
    assert "Stored demonstration result" in replayed.warnings[0]
    # A new inspection of the same package, not a duplicate of the old one.
    assert replayed.scan.scan_id != "RECORDED"


def test_upload_order_does_not_matter(workspace):
    """A browser sends files in whatever order it likes."""
    tmp_path, images = workspace
    record(tmp_path, images)
    assert fixtures.lookup(list(reversed(images)),
                           rules_version="pack-1", engine="rapidocr") is not None


def test_renamed_files_still_match_and_frames_point_at_the_upload(workspace):
    """Matched on content. The phone's filename is not evidence of anything."""
    tmp_path, images = workspace
    record(tmp_path, images)
    uploads = []
    for index, source in enumerate(images):
        destination = tmp_path / f"whatever-{index}.png"
        shutil.copy(source, destination)
        uploads.append(str(destination))
    replayed = fixtures.lookup(uploads, rules_version="pack-1", engine="rapidocr")
    assert replayed is not None
    # The recorded frames must be re-pointed at the files actually uploaded, or
    # the evidence images on the record would 404.
    assert sorted(replayed.scan.frames) == sorted(uploads)


def test_a_subset_or_a_superset_is_not_the_recorded_set(workspace):
    tmp_path, images = workspace
    record(tmp_path, images)
    assert fixtures.lookup(images[:2], rules_version="pack-1", engine="rapidocr") is None
    extra = tmp_path / "extra.png"
    Image.new("RGB", (40, 40), "yellow").save(extra)
    assert fixtures.lookup([*images, str(extra)],
                           rules_version="pack-1", engine="rapidocr") is None


def test_one_changed_pixel_takes_the_live_path(workspace):
    """Content addressing, not filename matching: a re-saved JPEG is a new image."""
    tmp_path, images = workspace
    record(tmp_path, images)
    Image.new("RGB", (40, 40), (255, 0, 1)).save(images[0])
    assert fixtures.lookup(images, rules_version="pack-1", engine="rapidocr") is None


def test_a_recording_from_another_pack_or_engine_is_refused(workspace):
    """The demonstration must show what *this* build decides.

    A stored result from an older rule pack is not that, and replaying it would
    be the one case where the recording really did misrepresent the system.
    """
    tmp_path, images = workspace
    record(tmp_path, images, rules_version="pack-1")
    assert fixtures.lookup(images, rules_version="pack-2", engine="rapidocr") is None
    assert fixtures.lookup(images, rules_version="pack-1", engine="tesseract") is None


def test_replay_can_be_switched_off(workspace, monkeypatch):
    tmp_path, images = workspace
    record(tmp_path, images)
    monkeypatch.setenv("TULA_DEMO_FIXTURES", "off")
    assert fixtures.lookup(images, rules_version="pack-1", engine="rapidocr") is None


def test_a_missing_or_corrupt_record_falls_back_to_live(workspace):
    """A broken fixture must degrade to analysis, never to an exception."""
    tmp_path, images = workspace
    record(tmp_path, images)
    (tmp_path / "demo-fixtures" / "qa.json").write_text("{ not json", encoding="utf-8")
    assert fixtures.lookup(images, rules_version="pack-1", engine="rapidocr") is None
    (tmp_path / "demo-fixtures" / "qa.json").unlink()
    assert fixtures.lookup(images, rules_version="pack-1", engine="rapidocr") is None


def test_no_manifest_at_all_is_simply_no_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures, "directory", lambda: tmp_path / "nothing-here")
    monkeypatch.delenv("TULA_DEMO_FIXTURES", raising=False)
    path = tmp_path / "a.png"
    Image.new("RGB", (10, 10), "white").save(path)
    assert fixtures.lookup([str(path)], rules_version="p", engine="e") is None
