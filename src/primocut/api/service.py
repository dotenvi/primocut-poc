"""Data assembly for the API (navigable report, list).

Reuses the Phase 0 models/rules: `youtube_url` (&t=), `format_timestamp` and the
cross-reference with chat peaks (🔥), just like the Markdown report.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import repo
from ..models import format_timestamp, youtube_url


def list_videos(s: Session, project_id: int) -> list[dict[str, Any]]:
    out = []
    for v in repo.list_project_lives(s, project_id):
        job = repo.latest_job(s, v.id)
        out.append({
            "id": v.id, "title": v.title, "channel": v.channel, "status": v.status,
            "progress": job.progress if job else 0,
            "stage": job.stage if job else "",
            "topics": len(repo.get_topic_rows(s, v.id)),
            "created_at": v.created_at.isoformat(),
        })
    return out


def video_detail(s: Session, project_id: int, video_id: str) -> dict[str, Any] | None:
    v = repo.get_project_live(s, project_id, video_id)
    if v is None:
        return None
    job = repo.latest_job(s, video_id)
    return {
        "id": v.id, "url": v.url, "title": v.title, "channel": v.channel,
        "duration": v.duration, "status": v.status,
        "job": None if job is None else {
            "stage": job.stage, "progress": job.progress, "status": job.status,
            "error": job.error, "warnings": job.warnings,
        },
    }


def build_report(s: Session, project_id: int, video_id: str) -> dict[str, Any] | None:
    v = repo.get_project_live(s, project_id, video_id)
    if v is None:
        return None
    transcript = repo.get_transcript(s, video_id)
    signal = repo.get_chat_signal(s, video_id)
    job = repo.latest_job(s, video_id)
    peaks = [p.ts for p in signal.peaks] if signal else []

    def fire(start: float, end: float) -> bool:
        return any(start <= ts <= end for ts in peaks)

    def yt(ts: float) -> str:
        return youtube_url(video_id, ts)

    topics = [{
        "id": r.id, "title": r.title, "summary": r.summary,
        "start": r.start, "end": r.end, "score": r.score,
        "start_hms": format_timestamp(r.start), "end_hms": format_timestamp(r.end),
        "youtube_url": yt(r.start), "fire": fire(r.start, r.end),
    } for r in repo.get_topic_rows(s, video_id)]

    return {
        "video": {
            "id": v.id, "url": v.url, "title": v.title, "channel": v.channel,
            "duration": v.duration, "status": v.status,
        },
        "job": None if job is None else {
            "stage": job.stage, "progress": job.progress, "status": job.status,
            "stage_history": job.stage_history or [],
        },
        "warnings": job.warnings if job else [],
        "chat_signal": None if signal is None else {
            "per_minute": [b.model_dump() for b in signal.per_minute],
            "peaks": [{"ts": p.ts, "intensity": p.intensity, "z": p.z,
                       "hms": format_timestamp(p.ts), "youtube_url": yt(p.ts)}
                      for p in signal.peaks],
        },
        "topics": topics,
        "transcript": [] if transcript is None else [{
            "index": seg.index, "start": seg.start, "end": seg.end, "text": seg.text,
            "start_hms": format_timestamp(seg.start), "youtube_url": yt(seg.start),
        } for seg in transcript.segments],
    }
