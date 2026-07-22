"""Data assembly for the API (navigable report, list).

Reuses the Phase 0 models/rules: `youtube_url` (&t=), `format_timestamp` and the
cross-reference with chat peaks (🔥), just like the Markdown report.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import clips as clips_repo
from ..db import cuts as cuts_repo
from ..db import exports as exports_repo
from ..db import packaging as pkg_repo
from ..db import repo
from ..db.models import CutRow, VideoRow
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


# ------------------------- Phase 3: cut room bench -------------------------


def _cut_summary(s: Session, cut: CutRow) -> dict[str, Any]:
    items = cuts_repo.list_items(s, cut.id)
    return {
        "id": cut.id, "name": cut.name, "status": cut.status,
        "created_at": cut.created_at.isoformat(),
        "item_count": len(items),
        "total_duration_s": sum(it.end_s - it.start_s for it in items),
    }


def list_cuts_payload(s: Session, video: VideoRow) -> list[dict[str, Any]]:
    """The live's cuts, ensuring the default `Cut 1` exists (lazy creation)."""
    cuts_repo.ensure_default_cut(s, video.id)
    return [_cut_summary(s, c) for c in cuts_repo.list_cuts(s, video.id)]


def export_summary(s: Session, video: VideoRow, cut: CutRow) -> dict[str, Any] | None:
    """The cut's latest export state for the UI to poll, or None if never exported."""
    row = exports_repo.get_export(s, cut.id)
    if row is None:
        return None
    base = (f"/api/projects/{video.project_id}/lives/{video.id}"
            f"/cuts/{cut.id}/export")
    ready = row.state == "done" and bool(row.zip_rel_path)
    return {
        "state": row.state,
        "error": row.error,
        "item_count": row.item_count,
        "skip_master": row.skip_master,
        "upscaled": row.upscaled,
        "download_url": f"{base}/download" if ready else None,
        "updated_at": row.updated_at.isoformat(),
    }


def _packaging_summary(row) -> dict[str, Any] | None:
    """The UI-facing view of a cut-packaging row (poll an in-flight generation)."""
    if row is None:
        return None
    return {"state": row.state, "error": row.error, "payload": row.payload,
            "updated_at": row.updated_at.isoformat()}


def bench_payload(s: Session, video: VideoRow, cut: CutRow,
                  settings: Settings | None = None) -> dict[str, Any]:
    """One-shot render payload for the decision bench: the active cut's ordered
    items, every cut for the selector, and the DERIVED state + signals of all the
    live's topics — assembled without an N+1 (three bulk reads over the live)."""
    settings = settings or get_settings()
    topic_rows = repo.get_topic_rows(s, video.id)
    titles = {t.id: t.title for t in topic_rows}
    triage = cuts_repo.triage_map(s, video.id)
    in_cut = cuts_repo.cut_topic_ids(s, cut.id)

    signal = repo.get_chat_signal(s, video.id)
    peaks = [p.ts for p in signal.peaks] if signal else []
    clips = clips_repo.clips_by_item(s, cut.id)
    packaging = pkg_repo.packaging_by_item(s, cut.id)
    # The deferred audience signal: the cached per-live match fills the slot the
    # decision bench reserved. Absent/empty → every topic reads audience_asked=False.
    match_row = pkg_repo.get_audience_match(s, video.id)
    audience = match_row.payload if match_row is not None else {}

    from ..worker.packaging_jobs import frames_dir_for

    def chat_peak(start: float, end: float) -> bool:
        return any(start <= ts <= end for ts in peaks)

    def packaging_of(it) -> dict:
        rows = packaging.get(it.id, {})
        base = (f"/api/projects/{video.project_id}/lives/{video.id}"
                f"/cuts/{cut.id}/items/{it.id}")
        fdir = frames_dir_for(settings, video.id, cut.id, it.position)
        frames = sorted(p.name for p in fdir.glob("frame-*.jpg")) if fdir.exists() else []
        return {
            "title": _packaging_summary(rows.get("title")),
            "thumb_brief": _packaging_summary(rows.get("thumb_brief")),
            "frame_ranking": _packaging_summary(rows.get("frame_ranking")),
            "frames": [{"name": n, "url": f"{base}/frames/{n}"} for n in frames],
        }

    def clip_of(it) -> dict | None:
        clip = clips.get(it.id)
        if clip is None:
            return None
        base = (f"/api/projects/{video.project_id}/lives/{video.id}"
                f"/cuts/{cut.id}/clips/{clip.id}")
        return {
            "id": clip.id, "state": clip.state, "error": clip.error,
            "filename": clip.filename, "encoder_used": clip.encoder_used,
            "size_bytes": clip.size_bytes,
            "download_url": f"{base}/download" if clip.state == "done" else None,
        }

    items = [{
        "id": it.id, "position": it.position, "topic_id": it.topic_id,
        "start_s": it.start_s, "end_s": it.end_s,
        "duration_s": it.end_s - it.start_s,
        "start_hms": format_timestamp(it.start_s), "end_hms": format_timestamp(it.end_s),
        # A topic item shows the topic title; a manual range (or a topic dropped by
        # a topic regeneration, degraded to a range) shows a neutral label.
        "title": titles.get(it.topic_id, "Manual range"),
        "clip": clip_of(it),
        "packaging": packaging_of(it),
    } for it in cuts_repo.list_items(s, cut.id)]

    topics = [{
        "id": t.id, "title": t.title, "start": t.start, "end": t.end,
        "start_hms": format_timestamp(t.start), "end_hms": format_timestamp(t.end),
        "duration_s": t.end - t.start,
        "state": cuts_repo.derive_state(t.id, in_cut, triage),
        # chat_peak is free existing data; audience_asked is now filled from the
        # cached per-live audience match (empty until the opt-in match runs).
        "signals": {"chat_peak": chat_peak(t.start, t.end),
                    "audience_asked": bool(audience.get(str(t.id))),
                    "audience_matches": audience.get(str(t.id), [])},
    } for t in topic_rows]

    return {
        "cut": {"id": cut.id, "name": cut.name, "status": cut.status,
                "created_at": cut.created_at.isoformat()},
        "cuts": [_cut_summary(s, c) for c in cuts_repo.list_cuts(s, video.id)],
        "items": items,
        "total_duration_s": sum(it["duration_s"] for it in items),
        "topics": topics,
        "export": export_summary(s, video, cut),
        # The audience-match state (for polling an in-flight opt-in match); None
        # until the match has ever run for this live.
        "audience_match": ({"state": match_row.state, "error": match_row.error}
                           if match_row is not None else None),
    }
