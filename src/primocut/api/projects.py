"""API router for Phase 2: projects CRUD and channel registration/detachment.

Built as a factory so it can receive the app's dependency callables without a
circular import with `app.py`.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import competitors as crepo
from ..youtube.client import YouTubeError


class ProjectIn(BaseModel):
    name: str
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class ChannelIn(BaseModel):
    url: str


class OwnChannelIn(BaseModel):
    channel_url: str
    handle: str = ""
    title: str = ""
    role: str | None = None  # 'main' | 'shorts' | free


class EnrichIn(BaseModel):
    kind: str  # "thumbnail" | "title" | "comments"


def _project_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "active": row.active,
        "created_at": row.created_at.isoformat(),
    }


def _channel_dict(row) -> dict[str, Any]:
    return {
        "channel_id": row.channel_id,
        "handle": row.handle,
        "title": row.title,
        "active": row.active,
        "backfill": {
            "state": row.backfill_state,
            "done": row.backfill_done,
            "total": row.backfill_total,
            "error": row.backfill_error,
        },
    }


def _own_channel_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_url": row.channel_url,
        "handle": row.handle,
        "title": row.title,
        "role": row.role,
        "added_at": row.added_at.isoformat(),
    }


def build_projects_router(
    *,
    get_db: Callable,
    get_queue_dep: Callable,
    get_settings_dep: Callable,
    get_youtube_client_dep: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/projects", status_code=201)
    def create_project(body: ProjectIn, db=Depends(get_db)) -> dict:
        with db.session() as s:
            try:
                row = crepo.create_project(s, body.name, body.description)
            except crepo.ProjectExistsError:
                raise HTTPException(status_code=409, detail="project name already exists")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return _project_dict(row)

    @router.get("/projects")
    def list_projects(db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            return [
                {**_project_dict(p), "channel_count": len(crepo.list_project_channels(s, p.id))}
                for p in crepo.list_projects(s)
            ]

    @router.get("/projects/{project_id}")
    def project_detail(project_id: int, db=Depends(get_db)) -> dict:
        with db.session() as s:
            row = crepo.get_project(s, project_id)
            if row is None:
                raise HTTPException(status_code=404, detail="project not found")
            channels = [_channel_dict(c) for c in crepo.list_project_channels(s, project_id)]
            return {**_project_dict(row), "channels": channels}

    @router.patch("/projects/{project_id}")
    def patch_project(project_id: int, body: ProjectPatch, db=Depends(get_db)) -> dict:
        with db.session() as s:
            row = crepo.update_project(
                s,
                project_id,
                name=body.name,
                description=body.description,
                active=body.active,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="project not found")
            return _project_dict(row)

    @router.delete("/projects/{project_id}")
    def remove_project(project_id: int, db=Depends(get_db)) -> dict:
        with db.session() as s:
            if not crepo.delete_project(s, project_id):
                raise HTTPException(status_code=404, detail="project not found")
        return {"project_id": project_id}

    @router.get("/projects/{project_id}/channels")
    def list_channels(project_id: int, db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            if crepo.get_project(s, project_id) is None:
                raise HTTPException(status_code=404, detail="project not found")
            return [_channel_dict(c) for c in crepo.list_project_channels(s, project_id)]

    @router.post("/projects/{project_id}/channels", status_code=201)
    def add_channel(
        project_id: int,
        body: ChannelIn,
        db=Depends(get_db),
        queue=Depends(get_queue_dep),
        settings=Depends(get_settings_dep),
        client=Depends(get_youtube_client_dep),
    ) -> dict:
        from ..worker.channels import register_channel
        from ..worker.queue import QueueUnavailableError

        try:
            return register_channel(
                project_id,
                body.url,
                settings=settings,
                database=db,
                queue=queue,
                client=client,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except YouTubeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except QueueUnavailableError as exc:
            raise HTTPException(
                status_code=503, detail=f"queue unavailable: {exc}"
            )

    @router.get("/projects/{project_id}/dashboard")
    def project_dashboard(
        project_id: int, db=Depends(get_db), settings=Depends(get_settings_dep)
    ) -> dict:
        from ..metrics import service as metrics_service

        with db.session() as s:
            data = metrics_service.project_dashboard(s, project_id, settings=settings)
        if data is None:
            raise HTTPException(status_code=404, detail="project not found")
        return data

    @router.post("/projects/{project_id}/enrich", status_code=202)
    def enrich_project_outliers(
        project_id: int,
        body: EnrichIn,
        db=Depends(get_db),
        queue=Depends(get_queue_dep),
        settings=Depends(get_settings_dep),
    ) -> dict:
        """Enrich this project's definitive outliers with an LLM — their packaging
        or, for kind="comments", the synthesis of the comments already collected.
        Opt-in: costs money, so it runs only from this explicit action
        (guideline 9). Selection is a pure read; the work goes to the queue."""
        from ..metrics import service as metrics_service
        from ..worker.queue import enqueue_enrichment

        with db.session() as s:
            video_ids = metrics_service.definitive_outlier_ids(
                s, project_id, settings=settings
            )
        if video_ids is None:
            raise HTTPException(status_code=404, detail="project not found")
        try:
            return enqueue_enrichment(
                video_ids, body.kind, settings=settings, queue=queue
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/projects/{project_id}/own-channels")
    def list_own_channels(project_id: int, db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            if crepo.get_project(s, project_id) is None:
                raise HTTPException(status_code=404, detail="project not found")
            return [_own_channel_dict(c) for c in crepo.list_own_channels(s, project_id)]

    @router.post("/projects/{project_id}/own-channels", status_code=201)
    def add_own_channel(project_id: int, body: OwnChannelIn, db=Depends(get_db)) -> dict:
        with db.session() as s:
            if crepo.get_project(s, project_id) is None:
                raise HTTPException(status_code=404, detail="project not found")
            try:
                row = crepo.create_own_channel(
                    s, project_id, channel_url=body.channel_url,
                    handle=body.handle, title=body.title, role=body.role,
                )
            except crepo.OwnChannelExistsError:
                raise HTTPException(
                    status_code=409, detail="channel already in this project"
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return _own_channel_dict(row)

    @router.delete("/projects/{project_id}/own-channels/{own_channel_id}")
    def delete_own_channel_endpoint(
        project_id: int, own_channel_id: int, db=Depends(get_db)
    ) -> dict:
        with db.session() as s:
            if not crepo.delete_own_channel(s, project_id, own_channel_id):
                raise HTTPException(status_code=404, detail="own channel not found")
        return {"own_channel_id": own_channel_id}

    @router.delete("/projects/{project_id}/channels/{channel_id}")
    def detach_channel_endpoint(
        project_id: int,
        channel_id: str,
        db=Depends(get_db),
        settings=Depends(get_settings_dep),
    ) -> dict:
        from ..worker.channels import detach_channel

        try:
            return detach_channel(
                project_id, channel_id, settings=settings, database=db
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")

    return router
