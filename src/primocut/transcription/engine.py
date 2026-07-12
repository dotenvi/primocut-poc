"""Abstração TranscriptionEngine (diretriz técnica #1).

O resto do sistema depende só desta interface, permitindo trocar faster-whisper
por WhisperX (Fase 2) sem tocar em pipeline/análise/relatório.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Transcript


@runtime_checkable
class TranscriptionEngine(Protocol):
    @property
    def name(self) -> str:
        """Identificador do engine/modelo (vai para Transcript.model e ao relatório)."""
        ...

    def transcribe(
        self, audio_path: Path, *, video_id: str, language: str = "pt"
    ) -> Transcript:
        """Transcreve o áudio e devolve um Transcript com segmentos indexados."""
        ...
