"""API router for the Phase 3 cut room (decision bench).

Project-scoped like the rest of the lives API: every route lives under
`/api/projects/{project_id}/lives/{live_id}`, and the leakage guard
(`repo.get_project_live`) makes a live from another project read as 404. This
realizes the scoped endpoints (cuts, items, reorder, triage, bench) approved for
Step 1 — the decision bench and virtual preview. NO rendering is triggered here.

Built as a factory so it receives the app's dependency callables without a
circular import with `app.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import clips as clips_repo
from ..db import cuts as cuts_repo
from ..db import exports as exports_repo
from ..db import repo
from . import service


class CutIn(BaseModel):
    name: str | None = None
    status: str | None = None


class PackagingIn(BaseModel):
    item_id: int | None = None  # None → every item of the cut
    k: int | None = None        # titles only: how many candidates
    count: int | None = None    # frames only: how many candidates per clip
    regenerate: bool = False


class AudienceMatchIn(BaseModel):
    regenerate: bool = False


class ItemIn(BaseModel):
    topic_id: int | None = None
    start_s: float | None = None
    end_s: float | None = None


class ReorderIn(BaseModel):
    item_ids: list[int]


class TriageIn(BaseModel):
    state: str


class RenderIn(BaseModel):
    item_id: int | None = None  # None → render every item of the cut
    regenerate: bool = False
    fast_copy: bool = False


class ExportIn(BaseModel):
    render_missing: bool = False  # render un-rendered items first (else it fails)
    skip_master: bool = False
    upscale_1440p: bool = False


def build_cuts_router(
    *,
    get_db: Callable,
    get_queue_dep: Callable,
    get_settings_dep: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/lives/{live_id}")

    def _live(s, project_id: int, live_id: str):
        """The live scoped to its project, or a 404 (the leakage guard)."""
        video = repo.get_project_live(s, project_id, live_id)
        if video is None:
            raise HTTPException(status_code=404, detail="live not found")
        return video

    def _cut(s, video, cut_id: int):
        cut = cuts_repo.get_cut(s, video.id, cut_id)
        if cut is None:
            raise HTTPException(status_code=404, detail="cut not found")
        return cut

    def _clip_path(settings, clip) -> Path:
        return Path(settings.clips_dir) / clip.rel_path

    def _unlink(settings, clip) -> None:
        """Remove a clip's file (best-effort); the row is handled by the caller."""
        try:
            _clip_path(settings, clip).unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------- Cuts -------------------------

    @router.get("/cuts")
    def list_cuts(project_id: int, live_id: str, db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            return service.list_cuts_payload(s, video)

    @router.post("/cuts", status_code=201)
    def create_cut(project_id: int, live_id: str, body: CutIn,
                   db=Depends(get_db)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = cuts_repo.create_cut(s, video.id, body.name or "Cut 1")
            return service._cut_summary(s, cut)

    @router.get("/cuts/{cut_id}")
    def get_bench(project_id: int, live_id: str, cut_id: int,
                  db=Depends(get_db), settings=Depends(get_settings_dep)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            return service.bench_payload(s, video, cut, settings)

    @router.patch("/cuts/{cut_id}")
    def patch_cut(project_id: int, live_id: str, cut_id: int, body: CutIn,
                  db=Depends(get_db)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            cuts_repo.update_cut(s, cut, name=body.name, status=body.status)
            return service._cut_summary(s, cut)

    @router.delete("/cuts/{cut_id}", status_code=204)
    def delete_cut(project_id: int, live_id: str, cut_id: int,
                   db=Depends(get_db), settings=Depends(get_settings_dep)) -> None:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            # Explicit deletion: drop the cut's clip files (the DB rows cascade).
            for clip in clips_repo.list_clips_for_cut(s, cut_id):
                _unlink(settings, clip)
            cuts_repo.delete_cut(s, cut)

    # ------------------------- Items -------------------------

    @router.post("/cuts/{cut_id}/items", status_code=201)
    def add_item(project_id: int, live_id: str, cut_id: int, body: ItemIn,
                 db=Depends(get_db), settings=Depends(get_settings_dep)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            try:
                cuts_repo.add_item(
                    s, cut, duration=video.duration, topic_id=body.topic_id,
                    start_s=body.start_s, end_s=body.end_s,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return service.bench_payload(s, video, cut, settings)

    @router.delete("/cuts/{cut_id}/items/{item_id}")
    def remove_item(project_id: int, live_id: str, cut_id: int, item_id: int,
                    db=Depends(get_db), settings=Depends(get_settings_dep)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            # Removing an item is an explicit action: take its clip file too (the
            # clip row cascades with the item). No automatic cleanup touches clips.
            clip = clips_repo.clip_for_item(s, item_id)
            if clip is not None:
                _unlink(settings, clip)
            if not cuts_repo.remove_item(s, cut, item_id):
                raise HTTPException(status_code=404, detail="item not found")
            return service.bench_payload(s, video, cut, settings)

    @router.patch("/cuts/{cut_id}/items")
    def reorder_items(project_id: int, live_id: str, cut_id: int, body: ReorderIn,
                      db=Depends(get_db), settings=Depends(get_settings_dep)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            try:
                cuts_repo.reorder_items(s, cut, body.item_ids)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return service.bench_payload(s, video, cut, settings)

    # ------------------------- Rendering -------------------------

    @router.post("/cuts/{cut_id}/render", status_code=202)
    def render(project_id: int, live_id: str, cut_id: int, body: RenderIn,
               db=Depends(get_db), queue=Depends(get_queue_dep),
               settings=Depends(get_settings_dep)) -> dict:
        """Render every item of the cut, or one (`item_id`), in the BACKGROUND.

        Returns immediately (202) so the user keeps triaging; progress is polled
        per clip via the bench. Pay-once by parameters unless `regenerate`."""
        from ..worker.queue import enqueue_render

        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
        item_ids = [body.item_id] if body.item_id is not None else None
        return enqueue_render(
            live_id, cut_id, item_ids=item_ids, regenerate=body.regenerate,
            fast_copy=body.fast_copy, settings=settings, database=db, queue=queue,
        )

    @router.get("/cuts/{cut_id}/clips")
    def list_clips(project_id: int, live_id: str, cut_id: int,
                   db=Depends(get_db)) -> list[dict]:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            base = (f"/api/projects/{project_id}/lives/{live_id}"
                    f"/cuts/{cut.id}/clips")
            return [{
                "id": c.id, "cut_item_id": c.cut_item_id, "position": c.position,
                "state": c.state, "error": c.error, "filename": c.filename,
                "encoder_used": c.encoder_used, "size_bytes": c.size_bytes,
                "download_url": f"{base}/{c.id}/download" if c.state == "done" else None,
            } for c in clips_repo.list_clips_for_cut(s, cut_id)]

    @router.get("/cuts/{cut_id}/clips/{clip_id}/download")
    def download_clip(project_id: int, live_id: str, cut_id: int, clip_id: int,
                      db=Depends(get_db), settings=Depends(get_settings_dep)):
        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
            clip = clips_repo.get_clip(s, cut_id, clip_id)
            if clip is None or clip.state != "done":
                raise HTTPException(status_code=404, detail="clip not available")
            path = _clip_path(settings, clip)
            filename = clip.filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="clip file missing")
        return FileResponse(str(path), media_type="video/mp4", filename=filename)

    @router.delete("/cuts/{cut_id}/clips/{clip_id}", status_code=204)
    def delete_clip(project_id: int, live_id: str, cut_id: int, clip_id: int,
                    db=Depends(get_db), settings=Depends(get_settings_dep)) -> None:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
            clip = clips_repo.get_clip(s, cut_id, clip_id)
            if clip is None:
                raise HTTPException(status_code=404, detail="clip not found")
            _unlink(settings, clip)
            clips_repo.delete_clip(s, clip)

    # ------------------------- Export -------------------------

    @router.post("/cuts/{cut_id}/export", status_code=202)
    def export(project_id: int, live_id: str, cut_id: int, body: ExportIn,
               db=Depends(get_db), queue=Depends(get_queue_dep),
               settings=Depends(get_settings_dep)) -> dict:
        """Package the cut into a deliverable bundle, in the BACKGROUND (202).

        The zip is produced for the UI download; progress is polled via the export
        status. An un-rendered item fails the export unless `render_missing`."""
        from ..worker.queue import enqueue_export

        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
        return enqueue_export(
            live_id, cut_id, render_missing=body.render_missing,
            skip_master=body.skip_master, upscale_1440p=body.upscale_1440p,
            settings=settings, database=db, queue=queue,
        )

    @router.get("/cuts/{cut_id}/export")
    def export_status(project_id: int, live_id: str, cut_id: int,
                      db=Depends(get_db)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            cut = _cut(s, video, cut_id)
            return service.export_summary(s, video, cut) or {"state": "none"}

    @router.get("/cuts/{cut_id}/export/download")
    def download_export(project_id: int, live_id: str, cut_id: int,
                        db=Depends(get_db), settings=Depends(get_settings_dep)):
        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
            row = exports_repo.get_export(s, cut_id)
            if row is None or row.state != "done" or not row.zip_rel_path:
                raise HTTPException(status_code=404, detail="export not available")
            path = Path(settings.exports_dir) / row.zip_rel_path
            filename = Path(row.zip_rel_path).name
        if not path.exists():
            raise HTTPException(status_code=404, detail="export file missing")
        return FileResponse(str(path), media_type="application/zip", filename=filename)

    # ------------------------- AI packaging (opt-in — costs LLM) -------------------------

    @router.get("/packaging/context")
    def packaging_context(project_id: int, live_id: str, db=Depends(get_db),
                          settings=Depends(get_settings_dep)) -> dict:
        """The niche's measured patterns (three lenses) that drive packaging — the
        read-only reference the Packaging kit shows. Unavailable when the live's
        project is missing (degrade with a message rather than invent patterns)."""
        from ..metrics import packaging_signals

        with db.session() as s:
            video = _live(s, project_id, live_id)
            ctx = packaging_signals.build_packaging_context(
                s, video.project_id, settings=settings)
        if ctx is None:
            return {"available": False,
                    "reason": "this live has no project to draw niche patterns from"}
        return {"available": True, **ctx}

    def _enqueue_packaging(kind: str, live_id: str, cut_id: int, body: PackagingIn,
                           queue, settings, db):
        from ..worker.queue import enqueue_packaging

        item_ids = [body.item_id] if body.item_id is not None else None
        try:
            return enqueue_packaging(
                live_id, cut_id, kind, item_ids=item_ids, k=body.k,
                regenerate=body.regenerate, settings=settings, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="cut not found")

    @router.post("/cuts/{cut_id}/packaging/titles", status_code=202)
    def gen_titles(project_id: int, live_id: str, cut_id: int, body: PackagingIn,
                   db=Depends(get_db), queue=Depends(get_queue_dep),
                   settings=Depends(get_settings_dep)) -> dict:
        """Propose K titles per clip in the BACKGROUND (opt-in, costs LLM)."""
        with db.session() as s:
            _cut(s, _live(s, project_id, live_id), cut_id)
        return _enqueue_packaging("title", live_id, cut_id, body, queue, settings, db)

    @router.post("/cuts/{cut_id}/packaging/brief", status_code=202)
    def gen_brief(project_id: int, live_id: str, cut_id: int, body: PackagingIn,
                  db=Depends(get_db), queue=Depends(get_queue_dep),
                  settings=Depends(get_settings_dep)) -> dict:
        """Generate a thumbnail brief per clip in the BACKGROUND (opt-in, costs LLM)."""
        with db.session() as s:
            _cut(s, _live(s, project_id, live_id), cut_id)
        return _enqueue_packaging("thumb_brief", live_id, cut_id, body, queue, settings, db)

    @router.post("/cuts/{cut_id}/packaging/frames", status_code=202)
    def extract_frames(project_id: int, live_id: str, cut_id: int, body: PackagingIn,
                       db=Depends(get_db), queue=Depends(get_queue_dep),
                       settings=Depends(get_settings_dep)) -> dict:
        """Extract candidate frames per clip — DETERMINISTIC (FFmpeg, no LLM cost)."""
        from ..worker.queue import enqueue_frame_extraction

        with db.session() as s:
            _cut(s, _live(s, project_id, live_id), cut_id)
        item_ids = [body.item_id] if body.item_id is not None else None
        try:
            return enqueue_frame_extraction(
                live_id, cut_id, item_ids=item_ids, count=body.count,
                regenerate=body.regenerate, settings=settings, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="cut not found")

    @router.post("/cuts/{cut_id}/packaging/frames/rank", status_code=202)
    def rank_frames(project_id: int, live_id: str, cut_id: int, body: PackagingIn,
                    db=Depends(get_db), queue=Depends(get_queue_dep),
                    settings=Depends(get_settings_dep)) -> dict:
        """Rank the extracted frames against the brief — SEPARATE opt-in vision pass."""
        from ..worker.queue import enqueue_frame_ranking

        with db.session() as s:
            _cut(s, _live(s, project_id, live_id), cut_id)
        item_ids = [body.item_id] if body.item_id is not None else None
        try:
            return enqueue_frame_ranking(
                live_id, cut_id, item_ids=item_ids, regenerate=body.regenerate,
                settings=settings, database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="cut not found")

    @router.get("/cuts/{cut_id}/items/{item_id}/frames/{name}")
    def download_frame(project_id: int, live_id: str, cut_id: int, item_id: int,
                       name: str, db=Depends(get_db),
                       settings=Depends(get_settings_dep)):
        """Serve one extracted candidate frame (used by the Packaging kit preview)."""
        from ..worker.packaging_jobs import frames_dir_for

        if "/" in name or ".." in name:  # path traversal guard
            raise HTTPException(status_code=404, detail="frame not found")
        with db.session() as s:
            video = _live(s, project_id, live_id)
            _cut(s, video, cut_id)
            item = s.get(cuts_repo.CutItemRow, item_id)
            if item is None or item.cut_id != cut_id:
                raise HTTPException(status_code=404, detail="item not found")
            position = item.position
        path = frames_dir_for(settings, live_id, cut_id, position) / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(str(path), media_type="image/jpeg", filename=name)

    @router.post("/audience-match", status_code=202)
    def audience_match(project_id: int, live_id: str, body: AudienceMatchIn,
                       db=Depends(get_db), queue=Depends(get_queue_dep),
                       settings=Depends(get_settings_dep)) -> dict:
        """Match the niche's audience requests to the live's topics (opt-in, cached
        per live). Fills the bench's `audience_asked` slot."""
        from ..worker.queue import enqueue_audience_match

        with db.session() as s:
            _live(s, project_id, live_id)
        try:
            return enqueue_audience_match(
                live_id, regenerate=body.regenerate, settings=settings,
                database=db, queue=queue)
        except KeyError:
            raise HTTPException(status_code=404, detail="live not found")

    # ------------------------- Triage -------------------------

    @router.patch("/topics/{topic_id}/triage")
    def set_triage(project_id: int, live_id: str, topic_id: int, body: TriageIn,
                   db=Depends(get_db)) -> dict:
        with db.session() as s:
            video = _live(s, project_id, live_id)
            try:
                cuts_repo.set_triage(s, video.id, topic_id, body.state)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return {"topic_id": topic_id, "state": body.state}

    return router
