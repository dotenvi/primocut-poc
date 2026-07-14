"""API router for Phase 2 metrics dashboards (channel + video).

Built as a factory (like `api/projects.py`) so it receives the app's dependency
callables without a circular import. The endpoints are PURE READS: every score is
computed on the fly over the snapshots — no writes.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from ..metrics import service as metrics_service


def build_metrics_router(*, get_db: Callable, get_settings_dep: Callable) -> APIRouter:
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

    return router
