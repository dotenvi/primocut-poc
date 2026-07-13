"""DAO: conversions between the pydantic models (Phase 0) and the ORM rows.

"Pipeline-facing" functions return pydantic models (to reuse the Phase 0
stages). "API-facing" functions return ORM rows (with id) for the API layer.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    ChatSignal,
    MinuteBucket,
    Peak,
    Segment,
    Topic,
    Transcript,
    Video,
    VideoStatus,
)
from .models import (
    ChatSignalRow,
    JobRow,
    TopicRow,
    TranscriptRow,
    VideoRow,
)

# ------------------------- Video -------------------------


def upsert_video(s: Session, video: Video) -> VideoRow:
    row = s.get(VideoRow, video.id)
    if row is None:
        row = VideoRow(id=video.id)
        s.add(row)
    row.url = video.url
    row.title = video.title
    row.channel = video.channel
    row.duration = video.duration
    row.status = video.status.value
    s.flush()
    return row


def get_video(s: Session, video_id: str) -> VideoRow | None:
    return s.get(VideoRow, video_id)


def list_videos(s: Session) -> list[VideoRow]:
    return list(
        s.execute(select(VideoRow).order_by(VideoRow.created_at.desc())).scalars()
    )


def set_video_status(s: Session, video_id: str, status: VideoStatus | str) -> None:
    row = s.get(VideoRow, video_id)
    if row is not None:
        row.status = status.value if isinstance(status, VideoStatus) else status


def update_video_meta(s: Session, video: Video) -> VideoRow:
    """Update metadata only (url/title/channel/duration); status is handled elsewhere."""
    row = s.get(VideoRow, video.id)
    if row is None:
        row = VideoRow(id=video.id, status=video.status.value)
        s.add(row)
    row.url = video.url
    row.title = video.title
    row.channel = video.channel
    row.duration = video.duration
    s.flush()
    return row


def delete_video(s: Session, video_id: str) -> bool:
    """Delete the video and EVERYTHING related (FK cascade: jobs/transcripts/topics/…)."""
    if s.get(VideoRow, video_id) is None:
        return False
    s.execute(delete(VideoRow).where(VideoRow.id == video_id))
    return True


def video_to_model(row: VideoRow) -> Video:
    return Video(
        id=row.id, url=row.url, title=row.title, channel=row.channel,
        duration=row.duration, status=VideoStatus(row.status),
    )


# ------------------------- Job -------------------------


def create_job(s: Session, video_id: str, rq_job_id: str | None = None) -> JobRow:
    row = JobRow(video_id=video_id, rq_job_id=rq_job_id, status="queued")
    s.add(row)
    s.flush()
    return row


def update_job(s: Session, job_id: int, **fields) -> JobRow | None:
    row = s.get(JobRow, job_id)
    if row is not None:
        for k, v in fields.items():
            setattr(row, k, v)
    return row


def append_stage(s: Session, job_id: int, stage: str) -> JobRow | None:
    """Record a stage transition (skipping consecutive duplicates) so per-step
    durations can be derived from the gaps between timestamps."""
    from datetime import datetime, timezone

    row = s.get(JobRow, job_id)
    if row is None:
        return None
    history = list(row.stage_history or [])
    if history and history[-1].get("stage") == stage:
        return row
    history.append({"stage": stage, "at": datetime.now(timezone.utc).isoformat()})
    row.stage_history = history
    return row


def latest_job(s: Session, video_id: str) -> JobRow | None:
    return s.execute(
        select(JobRow).where(JobRow.video_id == video_id).order_by(JobRow.id.desc())
    ).scalars().first()


# ------------------------- Transcript -------------------------


def save_transcript(s: Session, transcript: Transcript) -> None:
    row = s.execute(
        select(TranscriptRow).where(TranscriptRow.video_id == transcript.video_id)
    ).scalars().first()
    if row is None:
        row = TranscriptRow(video_id=transcript.video_id)
        s.add(row)
    row.language = transcript.language
    row.model = transcript.model
    row.segments = [seg.model_dump() for seg in transcript.segments]


def get_transcript(s: Session, video_id: str) -> Transcript | None:
    row = s.execute(
        select(TranscriptRow).where(TranscriptRow.video_id == video_id)
    ).scalars().first()
    if row is None:
        return None
    return Transcript(
        video_id=row.video_id, language=row.language, model=row.model,
        segments=[Segment(**d) for d in row.segments],
    )


# ------------------------- ChatSignal -------------------------


def save_chat_signal(s: Session, signal: ChatSignal) -> None:
    row = s.execute(
        select(ChatSignalRow).where(ChatSignalRow.video_id == signal.video_id)
    ).scalars().first()
    if row is None:
        row = ChatSignalRow(video_id=signal.video_id)
        s.add(row)
    row.per_minute = [b.model_dump() for b in signal.per_minute]
    row.peaks = [p.model_dump() for p in signal.peaks]


def get_chat_signal(s: Session, video_id: str) -> ChatSignal | None:
    row = s.execute(
        select(ChatSignalRow).where(ChatSignalRow.video_id == video_id)
    ).scalars().first()
    if row is None:
        return None
    return ChatSignal(
        video_id=row.video_id,
        per_minute=[MinuteBucket(**d) for d in row.per_minute],
        peaks=[Peak(**d) for d in row.peaks],
    )


# ------------------------- Topics -------------------------


def save_topics(s: Session, video_id: str, topics: list[Topic]) -> None:
    s.execute(delete(TopicRow).where(TopicRow.video_id == video_id))
    for i, t in enumerate(topics):
        s.add(TopicRow(
            video_id=video_id, ordinal=i, title=t.title, summary=t.summary,
            start=t.start, end=t.end, score=t.score,
        ))


def get_topics(s: Session, video_id: str) -> list[Topic]:
    rows = s.execute(
        select(TopicRow).where(TopicRow.video_id == video_id).order_by(TopicRow.ordinal)
    ).scalars()
    return [
        Topic(title=r.title, summary=r.summary, start=r.start, end=r.end, score=r.score)
        for r in rows
    ]


def get_topic_rows(s: Session, video_id: str) -> list[TopicRow]:
    return list(s.execute(
        select(TopicRow).where(TopicRow.video_id == video_id).order_by(TopicRow.ordinal)
    ).scalars())
