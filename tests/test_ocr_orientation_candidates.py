"""Bounded alternate rotation reads preserve uncertainty and cached-reader safety."""
from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import count

import numpy as np
import pytest
from PIL import Image

from tula.ocr.engines import RapidOcrEngine


class Classifier:
    cls_thresh = .9

    def __init__(self, label="180", score=.98):
        self.label, self.score = label, score

    def __call__(self, images):
        return images, [[self.label, self.score] for _ in images], .001


class Reader:
    """Explicit inference stub, never used as an actual-image accuracy claim."""

    use_cls = True

    def __init__(self, *, label="180", label_score=.98, score=.72, disagree=False):
        self.text_cls = Classifier(label, label_score)
        self.score, self.disagree = score, disagree
        self.calls = []

    def __call__(self, image, use_cls=None):
        automatic = self.use_cls if use_cls is None else use_cls
        if automatic:
            self.text_cls([image])
        text = "PACKED 18/04/2028" if automatic else "PACKED 14/08/2028"
        if self.disagree and len(self.calls) == 1:
            text = "PACKED 18/09/2028"
        self.calls.append({"automatic": automatic, "text": text})
        sx, sy = image.shape[1] / 600, image.shape[0] / 300
        box = [[x * sx, y * sy] for x, y in ((50, 100), (550, 100), (550, 145), (50, 145))]
        return ([[box, text, self.score if automatic else .88]], 0)


def read_capture(tmp_path, monkeypatch, reader, *, passes=3):
    path = tmp_path / "original.png"
    Image.new("RGB", (600, 300), "white").save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("TULA_OCR_MAX_PASSES", str(passes))
    from tula.ocr import auxiliary
    monkeypatch.setattr(auxiliary, "read_tesseract", lambda *args, **kwargs: None)
    engine = RapidOcrEngine()
    engine._reader = reader
    result = engine.read(str(path))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert len(result.passes) <= passes
    return result


def test_observed_flip_and_weak_numeric_result_add_one_candidate_with_real_source_boxes(tmp_path, monkeypatch):
    reader = Reader()
    classifier = reader.text_cls
    result = read_capture(tmp_path, monkeypatch, reader)
    assert sum(not call["automatic"] for call in reader.calls) == 1
    assert reader.text_cls is classifier and reader.use_cls
    alternate = next(p for p in result.passes if p["variant"] == "unrotated_candidate")
    assert alternate["automatic_crop_orientation"] is False
    observation = result.passes[0]["orientation_classification"]
    assert observation["decisions"][0]["rotated_180"] and observation["threshold"] == .9
    assert "bbox" not in observation["decisions"][0]  # Internal crop index is not a source location.
    assert result.quality["orientation_alternative"]["status"] == "completed"
    assert result.conflicts and result.lines[0].review_required
    assert result.lines[0].confidence == .54
    candidates = result.lines[0].alternatives
    assert {item["text"] for item in candidates} == {"PACKED 18/04/2028", "PACKED 14/08/2028"}
    assert {item["confidence"] for item in candidates} == {.72, .88}
    assert all(item["bbox"] == [50, 100, 550, 145] for item in candidates)


def test_credible_numeric_disagreement_also_schedules_check_when_model_scores_are_high(tmp_path, monkeypatch):
    reader = Reader(score=.97, disagree=True)
    result = read_capture(tmp_path, monkeypatch, reader)
    assert any(not call["automatic"] for call in reader.calls)
    assert result.lines[0].review_required and len(result.lines[0].alternatives) == 3


@pytest.mark.parametrize(("label", "score", "confidence"), [("0", .99, .72), ("180", .85, .72), ("180", .99, .99)])
def test_no_probe_without_actual_flip_or_without_uncertain_numeric_reading(tmp_path, monkeypatch,
                                                                          label, score, confidence):
    reader = Reader(label=label, label_score=score, score=confidence)
    result = read_capture(tmp_path, monkeypatch, reader)
    assert all(call["automatic"] for call in reader.calls)
    assert "orientation_alternative" not in result.quality


def test_nonnumeric_rotated_text_does_not_schedule_numeric_fallback(tmp_path, monkeypatch):
    class WordReader(Reader):
        def __call__(self, image, use_cls=None):
            raw = super().__call__(image, use_cls)
            raw[0][0][1] = "FRESH LABEL"
            return raw

    words = WordReader()
    result = read_capture(tmp_path, monkeypatch, words)
    assert result.passes[0]["orientation_classification"]["decisions"][0]["rotated_180"]
    assert "orientation_alternative" not in result.quality
    assert all(call["automatic"] for call in words.calls)


def test_exhausted_pass_budget_discloses_skipped_candidate_without_extra_inference(tmp_path, monkeypatch):
    reader = Reader()
    result = read_capture(tmp_path, monkeypatch, reader, passes=2)
    assert len(reader.calls) == 2 and all(call["automatic"] for call in reader.calls)
    assert result.quality["orientation_alternative"]["status"] == "budget_skipped"
    assert any("alternative orientation could not be checked" in warning for warning in result.warnings)


def test_exhausted_time_budget_does_not_run_alternate(tmp_path, monkeypatch):
    from tula.ocr import engines
    ticks = count(0, 2)
    monkeypatch.setattr(engines.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setenv("TULA_OCR_BUDGET_SECONDS", "3")
    reader = Reader()
    result = read_capture(tmp_path, monkeypatch, reader, passes=6)
    assert len(reader.calls) == 1 and reader.calls[0]["automatic"]
    assert result.quality["orientation_alternative"]["status"] == "budget_skipped"


def test_classifier_hook_is_restored_on_inference_failure():
    class FailingReader(Reader):
        def __call__(self, image, use_cls=None):
            self.text_cls([image])
            raise RuntimeError("recognizer failed")

    engine = RapidOcrEngine()
    engine._reader = reader = FailingReader()
    classifier = reader.text_cls
    with pytest.raises(RuntimeError, match="recognizer failed"):
        engine._recognize(np.zeros((20, 60, 3), dtype=np.uint8), observe_orientation=True)
    assert reader.text_cls is classifier


def test_failed_alternate_preserves_original_candidates_and_discloses_failure(tmp_path, monkeypatch):
    class FailedAlternate(Reader):
        def __call__(self, image, use_cls=None):
            if use_cls is False:
                raise RuntimeError("alternate unavailable")
            return super().__call__(image, use_cls)

    reader = FailedAlternate()
    result = read_capture(tmp_path, monkeypatch, reader)
    assert result.lines and result.quality["orientation_alternative"]["status"] == "failed"
    assert any("unrotated_candidate failed" in warning for warning in result.warnings)
    assert all(item["text"] == "PACKED 18/04/2028" for item in result.candidates)


def test_cached_reader_observation_is_serialized_and_does_not_leak_between_threads():
    class SlowReader(Reader):
        def __init__(self):
            super().__init__()
            self.active = self.peak = 0
            self.counter_lock = threading.Lock()

        def __call__(self, image, use_cls=None):
            with self.counter_lock:
                self.active += 1
                self.peak = max(self.active, self.peak)
            time.sleep(.01)
            try:
                return super().__call__(image, use_cls)
            finally:
                with self.counter_lock:
                    self.active -= 1

    engine = RapidOcrEngine()
    engine._reader = reader = SlowReader()
    classifier = reader.text_cls
    image = np.zeros((300, 600, 3), dtype=np.uint8)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: engine._recognize(image, observe_orientation=True), range(2)))
    assert reader.peak == 1 and reader.text_cls is classifier
    assert all(len(metadata["orientation_classification"]["decisions"]) == 1 for _, metadata in results)


def test_plain_callable_adapters_remain_backward_compatible():
    engine = RapidOcrEngine()
    engine._reader = lambda image: ([], 0)
    raw, metadata = engine._recognize(np.zeros((20, 60, 3), dtype=np.uint8), observe_orientation=True)
    assert raw == ([], 0) and metadata == {}
