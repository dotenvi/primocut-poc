"""Data models (pydantic), aligned with docs/PRIMOCUT.md §"Data model".

Important conventions from Phase 0:
- Timestamps are NEVER invented by the LLM. The analysis selects segment
  INDICES; `Transcript.resolve_span` converts those indices into seconds.
- YouTube links use integer seconds: `...&t=SECONDS`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def youtube_url(video_id: str, ts: float) -> str:
    """Clickable link to a moment in the video (integer seconds)."""
    return f"https://youtube.com/watch?v={video_id}&t={int(ts)}"


def format_timestamp(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
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


# States in which the video is being actively processed (not directly deletable).
ACTIVE_STATUSES = {
    VideoStatus.QUEUED.value,
    VideoStatus.DOWNLOADING.value,
    VideoStatus.TRANSCRIBING.value,
    VideoStatus.ANALYZING.value,
}


class TopicStatus(str, Enum):
    NEW = "new"
    MARKED = "marked"
    DISCARDED = "discarded"


class Video(BaseModel):
    id: str
    url: str
    title: str
    channel: str
    duration: float  # seconds
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
        """Convert a pair of segment indices into (start_sec, end_sec).

        Robust against the LLM: clamps out-of-range indices and sorts them
        if they come reversed. This is where we guarantee no timestamp is
        invented — we always derive them from real segments.
        """
        if not self.segments:
            return 0.0, 0.0
        lo, hi = sorted((start_index, end_index))
        last = len(self.segments) - 1
        lo = max(0, min(lo, last))
        hi = max(0, min(hi, last))
        return self.segments[lo].start, self.segments[hi].end

    def indexed_text(self) -> str:
        """Render the segments as `[i] M:SS text`, the LLM's input."""
        return "\n".join(
            f"[{s.index}] {format_timestamp(s.start)} {s.text}" for s in self.segments
        )


class ChatMessage(BaseModel):
    offset_seconds: float
    author: str = ""
    text: str = ""


class MinuteBucket(BaseModel):
    minute: int  # minute of the live (offset)
    msgs: int = 0
    laughs: int = 0
    emotes: int = 0


class Peak(BaseModel):
    ts: float  # seconds (start of the peak minute)
    intensity: float  # msgs/min at the peak
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


class Report(BaseModel):
    video: Video
    transcript_model: str
    chat_signal: ChatSignal | None = None
    topics: list[Topic] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def youtube_url(self, ts: float) -> str:
        return youtube_url(self.video.id, ts)
