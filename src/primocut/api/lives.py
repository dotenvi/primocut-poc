"""API router for the lives pipeline, scoped to a project (niche).

A live is CONTAINED in exactly one project, so every route lives under
`/api/projects/{project_id}/lives`. The project comes from the route context —
the user never picks it twice. Each `{live_id}` route runs the leakage guard
(`repo.get_project_live`): a live from another project reads as 404 here.

Built as a factory so it can receive the app's dependency callables without a
circular import with `app.py`.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import competitors as crepo
from ..db import repo
from . import service


class SubmitIn(BaseModel):
    url: str


def build_lives_router(
    *,
    get_db: Callable,
    get_queue_dep: Callable,
    get_stopper: Callable,
    get_settings_dep: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _require_project(s, project_id: int) -> None:
        if crepo.get_project(s, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")

    def _require_live(db, project_id: int, live_id: str) -> None:
        """Leakage guard: 404 unless the live exists AND belongs to this project."""
        with db.session() as s:
            if repo.get_project_live(s, project_id, live_id) is None:
                raise HTTPException(status_code=404, detail="live not found")

    @router.post("/projects/{project_id}/lives", status_code=201)
    def submit(project_id: int, body: SubmitIn, db=Depends(get_db),
               queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import enqueue_video

        try:
            return enqueue_video(body.url, project_id=project_id, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="project not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/projects/{project_id}/lives")
    def list_lives(project_id: int, db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            _require_project(s, project_id)
            return service.list_videos(s, project_id)

    @router.get("/projects/{project_id}/lives/{live_id}")
    def live_detail(project_id: int, live_id: str, db=Depends(get_db)) -> dict:
        with db.session() as s:
            data = service.video_detail(s, project_id, live_id)
        if data is None:
            raise HTTPException(status_code=404, detail="live not found")
        return data

    @router.get("/projects/{project_id}/lives/{live_id}/report")
    def report(project_id: int, live_id: str, db=Depends(get_db)) -> dict:
        with db.session() as s:
            data = service.build_report(s, project_id, live_id)
        if data is None:
            raise HTTPException(status_code=404, detail="live not found")
        return data

    @router.post("/projects/{project_id}/lives/{live_id}/topics", status_code=202)
    def generate_topics(project_id: int, live_id: str, db=Depends(get_db),
                        queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import generate_topics as gen_topics

        _require_live(db, project_id, live_id)
        return gen_topics(live_id, database=db, queue=queue)

    @router.post("/projects/{project_id}/lives/{live_id}/reenqueue", status_code=202)
    def reenqueue(project_id: int, live_id: str, db=Depends(get_db),
                  queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import reenqueue_video

        _require_live(db, project_id, live_id)
        return reenqueue_video(live_id, database=db, queue=queue)

    @router.post("/projects/{project_id}/lives/{live_id}/stop")
    def stop(project_id: int, live_id: str, db=Depends(get_db),
             stopper=Depends(get_stopper)) -> dict:
        from ..worker.queue import stop_video

        _require_live(db, project_id, live_id)
        return stop_video(live_id, database=db, stopper=stopper)

    @router.delete("/projects/{project_id}/lives/{live_id}")
    def remove(project_id: int, live_id: str, db=Depends(get_db),
               stopper=Depends(get_stopper), settings=Depends(get_settings_dep)) -> dict:
        from ..worker.queue import VideoBusyError, delete_video

        _require_live(db, project_id, live_id)
        try:
            return delete_video(live_id, settings=settings, database=db, stopper=stopper)
        except VideoBusyError:
            raise HTTPException(
                status_code=409,
                detail="live is being processed; stop it before deleting",
            )

    return router
