"""Modelos de dados (pydantic), alinhados a docs/PRIMOCUT.md §"Modelo de dados".

Convenções importantes da Fase 0:
- Timestamps NUNCA são inventados pelo LLM. A análise seleciona ÍNDICES de
  segmentos; `Transcript.resolve_span` converte esses índices em segundos.
- Links do YouTube usam segundos inteiros: `...&t=SEGUNDOS`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def youtube_url(video_id: str, ts: float) -> str:
    """Link clicável para um instante do vídeo (segundos inteiros)."""
    return f"https://youtube.com/watch?v={video_id}&t={int(ts)}"


def format_timestamp(seconds: float) -> str:
    """Formata segundos como H:MM:SS ou M:SS."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class VideoStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    DONE = "done"
    ERROR = "error"
    STOPPED = "stopped"


# Estados em que o vídeo está sendo processado ativamente (não deletável direto).
ACTIVE_STATUSES = {
    VideoStatus.QUEUED.value,
    VideoStatus.DOWNLOADING.value,
    VideoStatus.TRANSCRIBING.value,
    VideoStatus.ANALYZING.value,
}


class TopicStatus(str, Enum):
    NEW = "novo"
    MARKED = "marcado"
    DISCARDED = "descartado"


class Video(BaseModel):
    id: str
    url: str
    title: str
    channel: str
    duration: float  # segundos
    status: VideoStatus = VideoStatus.QUEUED


class Segment(BaseModel):
    index: int
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    video_id: str
    language: str
    model: str
    segments: list[Segment] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def resolve_span(self, start_index: int, end_index: int) -> tuple[float, float]:
        """Converte um par de índices de segmento em (início_seg, fim_seg).

        Robusto contra o LLM: clampa índices fora do intervalo e ordena
        se vierem invertidos. É aqui que garantimos que nenhum timestamp é
        inventado — sempre derivamos de segmentos reais.
        """
        if not self.segments:
            return 0.0, 0.0
        lo, hi = sorted((start_index, end_index))
        last = len(self.segments) - 1
        lo = max(0, min(lo, last))
        hi = max(0, min(hi, last))
        return self.segments[lo].start, self.segments[hi].end

    def indexed_text(self) -> str:
        """Renderiza os segmentos como `[i] M:SS texto`, entrada para o LLM."""
        return "\n".join(
            f"[{s.index}] {format_timestamp(s.start)} {s.text}" for s in self.segments
        )


class ChatMessage(BaseModel):
    offset_seconds: float
    author: str = ""
    text: str = ""


class MinuteBucket(BaseModel):
    minute: int  # minuto da live (offset)
    msgs: int = 0
    laughs: int = 0
    emotes: int = 0


class Peak(BaseModel):
    ts: float  # segundos (início do minuto de pico)
    intensity: float  # msgs/min no pico
    z: float  # z-score


class ChatSignal(BaseModel):
    video_id: str
    per_minute: list[MinuteBucket] = Field(default_factory=list)
    peaks: list[Peak] = Field(default_factory=list)


class Topic(BaseModel):
    title: str
    summary: str
    start: float
    end: float
    score: float = Field(ge=0.0, le=1.0)
    status: TopicStatus = TopicStatus.NEW


class CutMoment(BaseModel):
    title: str
    reason: str
    start: float
    end: float
    score: float = Field(ge=0.0, le=1.0)
    status: TopicStatus = TopicStatus.NEW


class Report(BaseModel):
    video: Video
    transcript_model: str
    chat_signal: ChatSignal | None = None
    topics: list[Topic] = Field(default_factory=list)
    moments: list[CutMoment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def youtube_url(self, ts: float) -> str:
        return youtube_url(self.video.id, ts)
