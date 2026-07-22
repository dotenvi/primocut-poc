"""WhisperX TranscriptionEngine — the second implementation of the interface.

This is the reason the `TranscriptionEngine` abstraction has existed since Phase 0
(technical guideline #2): faster-whisper can be swapped for WhisperX without the
rest of the system knowing. WhisperX adds a **wav2vec2 word-level alignment** pass
(PT-BR), which the Phase 3 render flow uses SURGICALLY — aligning only the small
window around a cut item to refine its boundaries, never re-transcribing a live.

Isolation: whisperx pins `huggingface-hub<1.0` and pulls a torch 2.8 / pyannote 4
/ CUDA stack that conflicts with the validated worker venv. So this module runs in
a SEPARATE venv (`.venv-align`) and is invoked out-of-process by `align_worker`;
the imports here are lazy, so importing the class in the worker venv is cheap and
never touches whisperx. The heavy path only ever executes inside the align venv.

Diarization extension point (NOT built): whisperx ships `DiarizationPipeline`
(pyannote, already pulled by this extra). A future step could assign a speaker to
each word here and expose it on the segments; it is deliberately out of scope for
the surgical boundary refinement and only documented as the plug-in point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import Segment, Transcript

logger = get_logger(__name__)


class Word:
    """A single aligned word with absolute-timeline start/end (seconds)."""

    __slots__ = ("text", "start", "end")

    def __init__(self, text: str, start: float, end: float) -> None:
        self.text = text
        self.start = start
        self.end = end

    def as_dict(self) -> dict:
        return {"text": self.text, "start": self.start, "end": self.end}


class WhisperXEngine:
    """TranscriptionEngine backed by WhisperX (transcribe + wav2vec2 alignment).

    `model=` / `align_model=` / factories are injectable so the logic is testable
    without whisperx or a GPU. In production everything is lazily loaded.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        model: Any | None = None,
        align_model: Any | None = None,
        whisperx_module: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._model = model
        self._align = align_model  # (align_model, metadata) tuple, injectable
        self._wx = whisperx_module  # injectable in tests; lazily imported otherwise

    @property
    def name(self) -> str:
        return f"whisperx:{self.settings.whisper_model}"

    # ------------------------- lazy loading -------------------------

    def _module(self) -> Any:
        if self._wx is None:
            import whisperx  # lazy: heavy import, only inside the align venv

            self._wx = whisperx
        return self._wx

    def _device(self) -> str:
        return self.settings.whisper_device

    def _load_model(self) -> Any:
        if self._model is None:
            wx = self._module()
            self._model = wx.load_model(
                self.settings.whisper_model,
                self._device(),
                compute_type=self.settings.whisper_compute_type,
                language=self.settings.whisper_language,
            )
        return self._model

    def _load_align(self, language: str) -> tuple[Any, Any]:
        if self._align is None:
            wx = self._module()
            model_name = self.settings.align_model or None
            self._align = wx.load_align_model(
                language_code=language, device=self._device(), model_name=model_name
            )
        return self._align

    # ------------------------- interface -------------------------

    def transcribe(
        self, audio_path: Path, *, video_id: str, language: str | None = None
    ) -> Transcript:
        """Full transcription + alignment (the TranscriptionEngine contract)."""
        language = language or self.settings.whisper_language
        words = self._align_words(audio_path, language=language)
        # Collapse the aligned words back into indexed segments for the Transcript.
        segments: list[Segment] = []
        for i, w in enumerate(words):
            segments.append(Segment(index=i, start=w.start, end=w.end, text=w.text))
        return Transcript(
            video_id=video_id, language=language, model=self.name, segments=segments
        )

    # ------------------------- surgical alignment -------------------------

    def align_words(
        self, audio_path: Path, *, language: str | None = None
    ) -> list[dict]:
        """Word-level timestamps for a (small) audio window, as plain dicts.

        This is what the render flow calls surgically: the audio is ONLY the
        item's window, so this never re-transcribes a live. Timestamps are
        relative to the given audio (t=0 = the window start); the caller maps
        them back to the live timeline.
        """
        language = language or self.settings.whisper_language
        return [w.as_dict() for w in self._align_words(audio_path, language=language)]

    def _align_words(self, audio_path: Path, *, language: str) -> list[Word]:
        wx = self._module()
        audio = wx.load_audio(str(audio_path))
        model = self._load_model()
        result = model.transcribe(audio, language=language)
        align_model, metadata = self._load_align(language)
        aligned = wx.align(
            result["segments"], align_model, metadata, audio,
            self._device(), return_char_alignments=False,
        )
        words: list[Word] = []
        for seg in aligned.get("segments", []):
            for w in seg.get("words", []):
                start, end = w.get("start"), w.get("end")
                if start is None or end is None:  # unaligned token (no timestamp)
                    continue
                words.append(Word(str(w.get("word", "")).strip(),
                                  float(start), float(end)))
        logger.info("WhisperX aligned %d words in the window.", len(words))
        return words
