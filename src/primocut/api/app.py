"""FastAPI application: submission, status and report."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..db import get_database
from . import service


class SubmitIn(BaseModel):
    url: str


# Dependencies (overridden in tests via app.dependency_overrides).
def get_db():
    return get_database()


def get_queue_dep():
    from ..worker.queue import get_queue

    return get_queue()


def get_stopper():
    from ..worker.queue import _default_stopper

    return _default_stopper


def get_settings_dep():
    return get_settings()


def create_app() -> FastAPI:
    app = FastAPI(title="PRIMOCUT", version="1.0")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/videos", status_code=201)
    def submit(body: SubmitIn, db=Depends(get_db), queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import enqueue_video

        try:
            return enqueue_video(body.url, database=db, queue=queue)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/videos")
    def list_videos(db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            return service.list_videos(s)

    @app.get("/api/videos/{video_id}")
    def video_detail(video_id: str, db=Depends(get_db)) -> dict:
        with db.session() as s:
            data = service.video_detail(s, video_id)
        if data is None:
            raise HTTPException(status_code=404, detail="video not found")
        return data

    @app.get("/api/videos/{video_id}/report")
    def report(video_id: str, db=Depends(get_db)) -> dict:
        with db.session() as s:
            data = service.build_report(s, video_id)
        if data is None:
            raise HTTPException(status_code=404, detail="video not found")
        return data

    @app.post("/api/videos/{video_id}/topics", status_code=202)
    def generate_topics(video_id: str, db=Depends(get_db), queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import generate_topics as gen_topics

        try:
            return gen_topics(video_id, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="video not found")

    @app.post("/api/videos/{video_id}/reenqueue", status_code=202)
    def reenqueue(video_id: str, db=Depends(get_db), queue=Depends(get_queue_dep)) -> dict:
        from ..worker.queue import reenqueue_video

        try:
            return reenqueue_video(video_id, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="video not found")

    @app.post("/api/videos/{video_id}/stop")
    def stop(video_id: str, db=Depends(get_db), stopper=Depends(get_stopper)) -> dict:
        from ..worker.queue import stop_video

        try:
            return stop_video(video_id, database=db, stopper=stopper)
        except KeyError:
            raise HTTPException(status_code=404, detail="video not found")

    @app.delete("/api/videos/{video_id}")
    def remove(video_id: str, db=Depends(get_db), stopper=Depends(get_stopper),
               settings=Depends(get_settings_dep)) -> dict:
        from ..worker.queue import VideoBusyError, delete_video

        try:
            return delete_video(video_id, settings=settings, database=db, stopper=stopper)
        except KeyError:
            raise HTTPException(status_code=404, detail="video not found")
        except VideoBusyError:
            raise HTTPException(
                status_code=409,
                detail="video is being processed; stop it before deleting",
            )

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """Serve the compiled SPA (M4), if present."""
    dist = Path(__file__).parent.parent / "webui" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")
