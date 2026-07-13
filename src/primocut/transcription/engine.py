"""TranscriptionEngine abstraction (technical guideline #1).

The rest of the system depends only on this interface, allowing faster-whisper
to be swapped for WhisperX (Phase 2) without touching pipeline/analysis/report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Transcript


@runtime_checkable
class TranscriptionEngine(Protocol):
    @property
    def name(self) -> str:
        """Engine/model identifier (goes to Transcript.model and the report)."""
        ...

    def transcribe(
        self, audio_path: Path, *, video_id: str, language: str = "pt"
    ) -> Transcript:
        """Transcribe the audio and return a Transcript with indexed segments."""
        ...
