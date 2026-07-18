"""FastAPI application: submission, status and report."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from ..config import get_settings
from ..db import get_database


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


def get_youtube_client_dep():
    """None → `register_channel` builds a real client from settings. Tests
    override this to inject a fake YouTube client (no network)."""
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="PRIMOCUT", version="1.0")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    from .events import build_events_router
    from .lives import build_lives_router
    from .metrics import build_metrics_router
    from .projects import build_projects_router

    app.include_router(build_events_router(get_db=get_db))
    app.include_router(
        build_lives_router(
            get_db=get_db,
            get_queue_dep=get_queue_dep,
            get_stopper=get_stopper,
            get_settings_dep=get_settings_dep,
        )
    )
    app.include_router(
        build_projects_router(
            get_db=get_db,
            get_queue_dep=get_queue_dep,
            get_settings_dep=get_settings_dep,
            get_youtube_client_dep=get_youtube_client_dep,
        )
    )
    app.include_router(
        build_metrics_router(
            get_db=get_db,
            get_queue_dep=get_queue_dep,
            get_settings_dep=get_settings_dep,
        )
    )

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """Serve the compiled SPA (M4), if present."""
    dist = Path(__file__).parent.parent / "webui" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="webui")
