"""Tests for the TranscriptionEngine contract and the faster-whisper mapping.

No model is downloaded and no GPU is used: we inject a fake model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from primocut.models import Segment, Transcript
from primocut.transcription.engine import TranscriptionEngine
from primocut.transcription.faster_whisper_engine import FasterWhisperEngine


@dataclass
class _RawSeg:
    start: float
    end: float
    text: str


class _FakeInfo:
    language = "pt"


class _FakeModel:
    """Imita faster_whisper.WhisperModel.transcribe()."""

    def __init__(self, segs):
        self._segs = segs
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return iter(self._segs), _FakeInfo()


class FakeEngine:
    """Minimal engine proving the Protocol is satisfiable/injectable."""

    name = "fake-engine"

    def transcribe(self, audio_path, *, video_id, language="pt") -> Transcript:
        return Transcript(
            video_id=video_id,
            language=language,
            model=self.name,
            segments=[Segment(index=0, start=0.0, end=1.0, text="oi")],
        )


def test_fake_engine_satisfies_protocol():
    assert isinstance(FakeEngine(), TranscriptionEngine)


def test_faster_whisper_engine_satisfies_protocol():
    assert isinstance(FasterWhisperEngine(model=_FakeModel([])), TranscriptionEngine)


def test_transcribe_maps_segments_with_sequential_indices():
    raw = [
        _RawSeg(0.0, 4.0, " hello "),
        _RawSeg(4.0, 9.0, "how are you"),
        _RawSeg(9.0, 15.0, "let's begin"),
    ]
    engine = FasterWhisperEngine(model=_FakeModel(raw))
    t = engine.transcribe(Path("/tmp/a.wav"), video_id="vid1")

    assert [s.index for s in t.segments] == [0, 1, 2]
    assert t.segments[0].text == "hello"  # trim applied
    assert t.segments[2].end == 15.0
    assert t.video_id == "vid1"
    assert t.language == "pt"
    assert t.model.startswith("faster-whisper:")


def test_transcribe_passes_vad_and_language():
    model = _FakeModel([])
    engine = FasterWhisperEngine(model=model)
    engine.transcribe(Path("/tmp/a.wav"), video_id="vid1", language="pt")
    _, kwargs = model.calls[0]
    assert kwargs["vad_filter"] is True
    assert kwargs["language"] == "pt"


def test_load_model_degrades_compute_type_on_gpu_failure():
    from primocut.config import Settings

    tried = []

    def factory(compute_type):
        tried.append(compute_type)
        if compute_type == "float16":
            raise RuntimeError("CUDA out of memory")
        return _FakeModel([])

    engine = FasterWhisperEngine(
        settings=Settings(whisper_device="cuda", whisper_compute_type="float16",
                          whisper_compute_fallbacks="float16,int8_float16,int8"),
        model_factory=factory,
    )
    model = engine._load_model()
    assert isinstance(model, _FakeModel)
    assert tried == ["float16", "int8_float16"]  # stopped at the first that worked
    assert engine.degradation_warnings  # recorded the degradation


def test_load_model_raises_when_all_compute_types_fail():
    import pytest

    from primocut.config import Settings

    def factory(compute_type):
        raise RuntimeError("boom")

    engine = FasterWhisperEngine(
        settings=Settings(whisper_device="cuda", whisper_compute_type="float16",
                          whisper_compute_fallbacks="float16,int8"),
        model_factory=factory,
    )
    with pytest.raises(RuntimeError, match="every compute_type"):
        engine._load_model()
