"""API router for Phase 2 metrics dashboards (channel + video) and the opt-in
enrichment of a single video.

Built as a factory (like `api/projects.py`) so it receives the app's dependency
callables without a circular import. The dashboards are PURE READS: every score is
computed on the fly over the snapshots. The one write path here is the enrichment
trigger, which only enqueues — it never calls an LLM inside the request.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import competitors as crepo
from ..metrics import service as metrics_service


class EnrichIn(BaseModel):
    kind: str  # "thumbnail" | "title"


def build_metrics_router(
    *, get_db: Callable, get_queue_dep: Callable, get_settings_dep: Callable
) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        """Enrich this video's packaging with an LLM. Opt-in: costs money, so it
        runs only from this explicit action (guideline 9)."""
        from ..worker.queue import enqueue_enrichment

        with db.session() as s:
            video = crepo.get_video(s, video_id)
            if video is None or video.channel_id != channel_id:
                raise HTTPException(status_code=404, detail="video not found")
        try:
            return enqueue_enrichment(
                [video_id], body.kind, settings=settings, queue=queue
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router
