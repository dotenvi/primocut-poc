"""RQ job / CLI core: export a cut into a deliverable bundle on disk.

Assembles the cut's rendered clips (copied, NEVER moved — the originals under
`clips_dir` are untouched) into a timestamped package dir under `exports_dir`,
with a per-clip SRT, a concat master (stream copy by default) and the two
manifests, optionally zipped. Runs in the BACKGROUND on the queue for the UI, and
INLINE for the CLI (both call `export_cut`).

Un-rendered items: by default the export FAILS with a clear message listing them
(export packages existing deliverables). `render_missing=True` renders the missing
items first (reusing the render job) and then packages.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from ..config import Settings, get_settings
from ..db import clips as clips_repo
from ..db import cuts as cuts_repo
from ..db import exports as exports_repo
from ..db import packaging as pkg_repo
from ..db import repo
from ..logging_conf import get_logger
from ..render import package as pkg
from ..render.encode import slugify

logger = get_logger(__name__)


def _clip_title(s, item) -> str:
    """The topic title for a topic item; a neutral label for a manual range."""
    if item.topic_id is not None:
        from ..db.models import TopicRow

        topic = s.get(TopicRow, item.topic_id)
        if topic is not None:
            return topic.title
    return "Manual range"


def _load_plan(s, video_id: str, cut_id: int) -> dict:
    """Snapshot everything the export needs into plain data (so the file IO runs
    outside the DB session): the video/cut metadata, the ordered items with their
    clip state and effective bounds, and the transcript segments."""
    cut = cuts_repo.get_cut(s, video_id, cut_id)
    if cut is None:
        raise ValueError(f"cut {cut_id} not found in live {video_id!r}")
    video = repo.get_video(s, video_id)
    clips = clips_repo.clips_by_item(s, cut_id)
    packaging = pkg_repo.packaging_by_item(s, cut_id)
    transcript = repo.get_transcript(s, video_id)

    items = []
    for it in cuts_repo.list_items(s, cut_id):
        clip = clips.get(it.id)
        clip_data = None
        if clip is not None:
            # Effective (refined) window when the WhisperX pass moved the cut;
            # a legacy 0/0 pair falls back to the requested bounds.
            has_eff = clip.render_end_s > clip.render_start_s
            clip_data = {
                "state": clip.state,
                "rel_path": clip.rel_path,
                "eff_start": clip.render_start_s if has_eff else clip.start_s,
                "eff_end": clip.render_end_s if has_eff else clip.end_s,
            }
        # AI packaging payloads (present only where the user generated them).
        pk = packaging.get(it.id, {})
        items.append({
            "item_id": it.id,
            "position": it.position,
            "topic_id": it.topic_id,
            "title": _clip_title(s, it),
            "kind": "topic" if it.topic_id is not None else "manual",
            "clip": clip_data,
            "packaging": {
                "title": pk["title"].payload if "title" in pk else None,
                "thumb_brief": pk["thumb_brief"].payload if "thumb_brief" in pk else None,
                "frame_ranking": pk["frame_ranking"].payload if "frame_ranking" in pk else None,
            },
        })

    return {
        "video": SimpleNamespace(
            id=video.id, url=video.url, title=video.title,
            channel=video.channel, duration=video.duration,
        ),
        "cut": SimpleNamespace(id=cut.id, name=cut.name),
        "items": items,
        "segments": list(transcript.segments) if transcript else [],
    }


def _missing_ids(items: list[dict]) -> list[int]:
    """Item ids without a `done` clip (unrendered or errored)."""
    return [it["item_id"] for it in items
            if it["clip"] is None or it["clip"]["state"] != "done"]


def export_cut(
    video_id: str,
    cut_id: int,
    *,
    render_missing: bool = False,
    skip_master: bool = False,
    upscale_1440p: bool = False,
    make_zip: bool = True,
    settings: Settings | None = None,
    database=None,
    render_fn: Callable | None = None,
    master_runner: Callable | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict:
    """Package a cut. Business failures (unrendered items, a bad flag combo) are
    recorded on the export row and returned as `{"state": "error", ...}`; only an
    unexpected error re-raises (after marking the row)."""
    import subprocess

    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    master_runner = master_runner or subprocess.run
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    with database.session() as s:
        plan = _load_plan(s, video_id, cut_id)
    items = plan["items"]

    with database.session() as s:
        exports_repo.reset_to_queued(
            s, video_id=video_id, cut_id=cut_id, item_count=len(items),
            skip_master=skip_master, upscaled=upscale_1440p,
        )

    def _error(msg: str) -> dict:
        logger.warning("export(cut %s): %s", cut_id, msg)
        with database.session() as s:
            exports_repo.set_state(s, cut_id, "error", error=msg)
        return {"cut_id": cut_id, "state": "error", "error": msg}

    if upscale_1440p and skip_master:
        return _error("--upscale-1440p needs the master; it cannot be combined "
                      "with skipping the master.")
    if not items:
        return _error("the cut has no items to export.")

    # Un-rendered items: fail clearly by default, or render them first when asked.
    missing = _missing_ids(items)
    if missing:
        if not render_missing:
            names = ", ".join(f"#{i}" for i in missing)
            return _error(f"{len(missing)} item(s) not rendered: {names}. Render "
                          "them first, or export with render_missing.")
        render_fn = render_fn or _default_render
        logger.info("export(cut %s): rendering %d missing item(s) first",
                    cut_id, len(missing))
        render_fn(video_id, cut_id, missing, settings=settings, database=database)
        with database.session() as s:
            plan = _load_plan(s, video_id, cut_id)
        items = plan["items"]
        still = _missing_ids(items)
        if still:
            names = ", ".join(f"#{i}" for i in still)
            return _error(f"{len(still)} item(s) still not rendered after the "
                          f"render pass: {names}.")

    try:
        return _package(
            plan, items, video_id, cut_id, settings=settings, database=database,
            skip_master=skip_master, upscale_1440p=upscale_1440p,
            make_zip=make_zip, master_runner=master_runner, now_fn=now_fn,
        )
    except Exception as exc:  # unexpected (FFmpeg/IO) — record and re-raise
        logger.error("export(cut %s) failed: %s", cut_id, exc)
        with database.session() as s:
            exports_repo.set_state(s, cut_id, "error", error=str(exc))
        raise


def _default_render(video_id, cut_id, item_ids, *, settings, database):
    from .render_jobs import render_clips

    render_clips(video_id, cut_id, item_ids, settings=settings, database=database)


def _packaging_meta(it, position, pkg_dir, *, settings, video_id, cut_id) -> dict:
    """The manifest packaging block for one item: titles + brief + copied frames.

    Titles/brief are flattened for the human manifest (`titles` list,
    `thumbnail_brief` one-liner) AND kept in full (`*_detail`). Candidate frames
    are COPIED into `NNN-frames/` beside the clip (originals under clips_dir stay
    put). Everything degrades to empty when the user never generated it.
    """
    from .packaging_jobs import frames_dir_for

    pk = it.get("packaging") or {}
    meta: dict = {"titles": [], "titles_detail": [], "thumbnail_brief": "",
                  "thumbnail_brief_detail": None, "frames": [], "best_frame": ""}

    title_payload = pk.get("title")
    if title_payload:
        detail = title_payload.get("titles") or []
        meta["titles"] = [t.get("title", "") for t in detail]
        meta["titles_detail"] = detail

    brief = pk.get("thumb_brief")
    if brief:
        meta["thumbnail_brief"] = _brief_oneliner(brief)
        meta["thumbnail_brief_detail"] = brief

    # Copy the candidate frames into the bundle, referencing them relative to it.
    src_dir = frames_dir_for(settings, video_id, cut_id, it.get("position", position))
    frame_files = sorted(src_dir.glob("frame-*.jpg")) if src_dir.exists() else []
    if frame_files:
        rel_dir = f"{position + 1:03d}-frames"
        (pkg_dir / rel_dir).mkdir(parents=True, exist_ok=True)
        for f in frame_files:
            shutil.copy2(f, pkg_dir / rel_dir / f.name)
            meta["frames"].append(f"{rel_dir}/{f.name}")
        ranking = pk.get("frame_ranking") or {}
        best = ranking.get("best_filename")
        if best and (src_dir / best).exists():
            meta["best_frame"] = f"{rel_dir}/{best}"
    return meta


def _brief_oneliner(brief: dict) -> str:
    """A compact human summary of a thumbnail brief for the Markdown manifest."""
    comp = brief.get("composition") or {}
    text = brief.get("text") or {}
    bits = [f"{comp.get('face_count', '?')} face(s)",
            f"expression: {comp.get('expression', '?')}",
            f"framing: {comp.get('framing', '?')}"]
    suggestion = (text.get("suggestion") or "").strip()
    if suggestion:
        bits.append(f"text: {suggestion!r}")
    return ", ".join(bits)


def _package(plan, items, video_id, cut_id, *, settings, database, skip_master,
             upscale_1440p, make_zip, master_runner, now_fn) -> dict:
    with database.session() as s:
        exports_repo.set_state(s, cut_id, "packaging")

    video, cut = plan["video"], plan["cut"]
    segments = plan["segments"]
    exports_dir = Path(settings.exports_dir)
    clips_dir = Path(settings.clips_dir)

    moment = now_fn()
    generated_at = moment.isoformat()
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    pkg_rel = f"{video_id}/{cut_id}/{slugify(cut.name)}-{stamp}"
    pkg_dir = exports_dir / pkg_rel
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Copy each clip (in composition order) + write its SRT, at export-time NNN.
    mp4_names: list[str] = []
    items_meta: list[dict] = []
    for position, it in enumerate(items):
        clip = it["clip"]
        mp4_name = pkg.clip_export_name(position, it["title"], "mp4")
        srt_name = pkg.clip_export_name(position, it["title"], "srt")
        # NEVER move the original: copy it into the package.
        shutil.copy2(clips_dir / clip["rel_path"], pkg_dir / mp4_name)
        start, end = clip["eff_start"], clip["eff_end"]
        (pkg_dir / srt_name).write_text(
            pkg.srt_for_clip(segments, start, end), encoding="utf-8")
        mp4_names.append(mp4_name)
        packaging_meta = _packaging_meta(
            it, position, pkg_dir, settings=settings, video_id=video_id, cut_id=cut_id)
        items_meta.append({
            "position": position + 1,
            "mp4": mp4_name,
            "srt": srt_name,
            "title": it["title"],
            "kind": it["kind"],
            "topic_id": it["topic_id"],
            "start_s": start,
            "end_s": end,
            "duration_s": max(0.0, end - start),
            # AI packaging (this step): titles/brief/frames when generated, empty
            # (present-but-empty) otherwise, so the manifest shape is stable.
            "packaging": packaging_meta,
        })

    master_info = None
    if not skip_master:
        concat = pkg_dir / "concat.txt"
        concat.write_text(pkg.build_concat_file(mp4_names), encoding="utf-8")
        # FFmpeg runs with cwd = the package dir (so the concat listing's bare clip
        # names resolve there), so the concat/master paths handed to it MUST be
        # absolute — a package-relative path (the default `exports_dir="exports"`)
        # would otherwise be resolved against that cwd and vanish, failing the
        # master. `.absolute()` (not `.resolve()`) leaves symlinks untouched, so it
        # is a no-op when exports_dir is already absolute.
        pkg.concat_master(
            concat.absolute(), (pkg_dir / "master.mp4").absolute(),
            upscale_1440p=upscale_1440p, settings=settings,
            runner=master_runner, cwd=pkg_dir.absolute(),
        )
        concat.unlink(missing_ok=True)  # the listing is not part of the deliverable
        master_info = {
            "filename": "master.mp4",
            "upscaled": upscale_1440p,
            "resolution": f"{settings.export_upscale_height}p" if upscale_1440p
                          else "original",
        }

    data = pkg.build_manifest_data(
        video=video, cut=cut, items=items_meta, master=master_info,
        generated_at=generated_at,
    )
    (pkg_dir / "manifest.json").write_text(pkg.manifest_json_text(data), encoding="utf-8")
    (pkg_dir / "manifest.md").write_text(pkg.render_manifest_md(data), encoding="utf-8")

    zip_rel = ""
    if make_zip:
        shutil.make_archive(str(pkg_dir), "zip", root_dir=str(pkg_dir))
        # The archive lands at `<exports_dir>/<pkg_rel>.zip`. Derive the relative
        # path deterministically: `make_archive` with a `root_dir` returns an
        # ABSOLUTE path, so `relative_to(exports_dir)` would blow up whenever
        # exports_dir is relative (the default `"exports"`).
        zip_rel = f"{pkg_rel}.zip"

    with database.session() as s:
        exports_repo.set_state(
            s, cut_id, "done", package_rel_path=pkg_rel, zip_rel_path=zip_rel,
            error="",
        )
    logger.info("export(cut %s) done: %d item(s) -> %s%s%s", cut_id, len(items),
                pkg_rel, "" if master_info else " (no master)",
                " [1440p]" if upscale_1440p else "")
    return {
        "cut_id": cut_id,
        "state": "done",
        "items": len(items),
        "package_dir": str(pkg_dir),
        "package_rel_path": pkg_rel,
        "zip_path": str(exports_dir / zip_rel) if zip_rel else None,
        "master": bool(master_info),
        "upscaled": upscale_1440p,
    }
