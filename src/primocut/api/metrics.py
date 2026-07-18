"""API router for Phase 2 metrics dashboards (channel + video) and the opt-in
enrichment of a single video.

Built as a factory (like `api/projects.py`) so it receives the app's dependency
callables without a circular import. The dashboards are PURE READS: every score is
computed on the fly over the snapshots. The one write path here is the enrichment
trigger, which only enqueues — it never calls an LLM inside the request.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import competitors as crepo
from ..metrics import service as metrics_service

_FORMATS = ("long", "short")


def _require_format(fmt: str) -> None:
    if fmt not in _FORMATS:
        raise HTTPException(status_code=400, detail="format must be 'long' or 'short'")


class EnrichIn(BaseModel):
    kind: str  # "thumbnail" | "title" | "comments"
    # Comments only: re-fetch them from the API before synthesising again. The
    # packaging kinds have nothing to re-fetch and reject it (400).
    regenerate: bool = False


def build_metrics_router(
    *, get_db: Callable, get_queue_dep: Callable, get_settings_dep: Callable
) -> APIRouter:
    router = APIRouter(prefix="/api")

    # Declared before the `{channel_id}` routes so the static path wins the match.
    @router.get("/channels/distribution")
    def distribution_batch(
        channel_ids: str = Query(..., description="comma-separated channel ids"),
        format: str = "long",
        db=Depends(get_db),
        settings=Depends(get_settings_dep),
    ) -> dict:
        """Batch "channel spread" for the Radar: one call for every visible card's
        channel. Query volume is bounded by the number of channels, never by
        videos-per-channel (no N+1, no snapshot scan)."""
        _require_format(format)
        ids = [c for c in channel_ids.split(",") if c]
        with db.session() as s:
            return metrics_service.channel_distributions(s, ids, fmt=format, settings=settings)

    @router.get("/channels/{channel_id}/distribution")
    def distribution(
        channel_id: str,
        format: str = "long",
        db=Depends(get_db),
        settings=Depends(get_settings_dep),
    ) -> dict:
        """One channel's "channel spread" for the video-detail strip: one call per
        page. Returns the window histogram + band thresholds; the caller marks a
        video by its own view count against `bucket_edges`."""
        _require_format(format)
        with db.session() as s:
            data = metrics_service.channel_distribution(
                s, channel_id, fmt=format, settings=settings
            )
        if data is None:
            raise HTTPException(status_code=404, detail="channel not found")
        return data

    @router.get("/channels/{channel_id}/dashboard")
    def channel_dashboard(
        channel_id: str, db=Depends(get_db), settings=Depends(get_settings_dep)
    ) -> dict:
        with db.session() as s:
            data = metrics_service.channel_dashboard(s, channel_id, settings=settings)
        if data is None:
            raise HTTPException(status_code=404, detail="channel not found")
        return data

    @router.get("/channels/{channel_id}/videos/{video_id}")
    def video_report(
        channel_id: str,
        video_id: str,
        db=Depends(get_db),
        settings=Depends(get_settings_dep),
    ) -> dict:
        with db.session() as s:
            data = metrics_service.video_report(s, channel_id, video_id, settings=settings)
        if data is None:
            raise HTTPException(status_code=404, detail="video not found")
        return data

    @router.post("/channels/{channel_id}/videos/{video_id}/enrich", status_code=202)
    def enrich_video(
        channel_id: str,
        video_id: str,
        body: EnrichIn,
        db=Depends(get_db),
        queue=Depends(get_queue_dep),
        settings=Depends(get_settings_dep),
    ) -> dict:
        """Enrich this video's packaging, or synthesise its comments, with an LLM.
        Opt-in: costs money, so it runs only from this explicit action
        (guideline 9)."""
        from ..worker.queue import enqueue_enrichment

        with db.session() as s:
            video = crepo.get_video(s, video_id)
            if video is None or video.channel_id != channel_id:
                raise HTTPException(status_code=404, detail="video not found")
        try:
            return enqueue_enrichment(
                [video_id], body.kind, regenerate=body.regenerate,
                settings=settings, queue=queue,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
