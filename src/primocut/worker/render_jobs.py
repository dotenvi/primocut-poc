"""RQ job: render the clips of a cut's items (section download + FFmpeg re-encode).

Runs in the BACKGROUND on the existing queue, so the UI keeps letting the user
triage while clips render. Resilience (DEVELOPMENT.md §4): per-item isolation —
exactly like the enrichment batch. A clip that fails is logged, marked
`state="error"` with its message (what the UI renders), and the batch moves on;
one bad item never wastes the others.

Pay-once cache: an item already rendered `done` with the same `params_hash`
(range + padding + encode profile) is skipped, unless `regenerate` is set. A pure
reorder (same params, different position) renames the existing file to the new
`NNN-slug.mp4` without re-encoding.

The section is an EPHEMERAL intermediate: it is downloaded into a throwaway temp
dir and discarded when the item is done. The clip MP4 is the permanent
deliverable, laid out under `<clips_dir>/<video_id>/<cut_id>/NNN-slug.mp4`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Iterable

from ..config import Settings, get_settings
from ..db import clips as clips_repo
from ..db import cuts as cuts_repo
from ..logging_conf import get_logger
from ..render import align as align_mod
from ..render import encode as encode_mod
from ..render import section as section_mod
from ..render.encode import clip_filename

logger = get_logger(__name__)


def _clip_title(s, item) -> str:
    """The topic title for a topic item; a neutral label for a manual range (or a
    topic item degraded to a range by a topic regeneration)."""
    if item.topic_id is not None:
        from ..db.models import TopicRow

        topic = s.get(TopicRow, item.topic_id)
        if topic is not None:
            return topic.title
    return "Manual range"


def render_clips(
    video_id: str,
    cut_id: int,
    item_ids: Iterable[int] | None = None,
    *,
    regenerate: bool = False,
    fast_copy: bool = False,
    settings: Settings | None = None,
    database=None,
    download_section_fn: Callable = section_mod.download_section,
    encode_clip_fn: Callable = encode_mod.encode_clip,
    refine_bounds_fn: Callable = align_mod.refine_bounds,
    gpu_lock=None,
    tempdir_factory: Callable = tempfile.TemporaryDirectory,
) -> dict:
    """Render each requested item's clip, with per-item isolation and the cache."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    profile = "copy" if fast_copy else settings.render_encoder
    clips_root = Path(settings.clips_dir)
    margin = settings.render_section_margin_s
    # Surgical boundary refinement runs only when an align venv is configured
    # (PRIMOCUT_ALIGN_PYTHON) or a caller injected a lock (the tests). It shares
    # the GPU with transcription, so it holds the same distributed lock.
    align_enabled = bool(settings.align_python) or gpu_lock is not None
    if align_enabled and gpu_lock is None:
        from ..gpu import redis_gpu_lock

        gpu_lock = redis_gpu_lock(settings)

    # Snapshot the work list up front (id, position, range, title, url, duration).
    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise ValueError(f"cut {cut_id} not found in live {video_id!r}")
        from ..db import repo

        video = repo.get_video(s, video_id)
        url = video.url if video else f"https://www.youtube.com/watch?v={video_id}"
        duration = float(video.duration) if video else 0.0
        wanted = set(item_ids) if item_ids is not None else None
        jobs = []
        for it in cuts_repo.list_items(s, cut_id):
            if wanted is not None and it.id not in wanted:
                continue
            jobs.append({
                "item_id": it.id, "position": it.position,
                "start_s": it.start_s, "end_s": it.end_s,
                "title": _clip_title(s, it),
            })

    rendered = skipped = failed = 0
    for j in jobs:
        try:
            if _render_one(
                database, settings, video_id, cut_id, j, url=url, duration=duration,
                margin=margin, profile=profile, clips_root=clips_root,
                regenerate=regenerate,
                align_enabled=align_enabled, gpu_lock=gpu_lock,
                download_section_fn=download_section_fn,
                encode_clip_fn=encode_clip_fn, refine_bounds_fn=refine_bounds_fn,
                tempdir_factory=tempdir_factory,
            ):
                rendered += 1
            else:
                skipped += 1
        except Exception as exc:  # per-item isolation: log, record, keep going
            logger.error("render: item %s of cut %s failed: %s", j["item_id"], cut_id, exc)
            failed += 1
            try:
                with database.session() as s:
                    clip = clips_repo.clip_for_item(s, j["item_id"])
                    if clip is not None:
                        clips_repo.set_state(s, clip.id, "error", error=str(exc))
            except Exception as inner:  # a clip we cannot even flag must not abort the batch
                logger.error("render: could not record failure of item %s: %s",
                             j["item_id"], inner)
            continue

    logger.info("render(cut %s) done: %d rendered, %d cached, %d failed",
                cut_id, rendered, skipped, failed)
    return {"cut_id": cut_id, "rendered": rendered, "skipped": skipped, "failed": failed}


def _render_one(
    database, settings, video_id, cut_id, j, *, url, duration, margin, profile,
    clips_root, regenerate, align_enabled, gpu_lock, download_section_fn,
    encode_clip_fn, refine_bounds_fn, tempdir_factory,
) -> bool:
    """Render one item. Returns True if it re-encoded, False if the cache served it."""
    item_id = j["item_id"]
    start_s, end_s = float(j["start_s"]), float(j["end_s"])
    position = j["position"]
    filename = clip_filename(position, j["title"])
    rel_path = f"{video_id}/{cut_id}/{filename}"
    dest = clips_root / rel_path
    # The tolerance is part of the pay-once key (a new tolerance can move the exact
    # cut points, so it must re-render); it is 0 when refinement is off.
    tolerance = settings.align_tolerance_s if align_enabled else 0.0
    phash = clips_repo.params_hash(start_s, end_s, margin, profile, tolerance)

    # Pay-once cache: same item, same parameters, file present → skip the encode.
    with database.session() as s:
        existing = clips_repo.clip_for_item(s, item_id)
        cache_hit = (
            not regenerate and existing is not None
            and existing.state == "done" and existing.params_hash == phash
        )
        old_rel = existing.rel_path if existing is not None else ""
        old_size = existing.size_bytes if existing is not None else 0
    if cache_hit and (clips_root / old_rel).exists():
        # A pure reorder changed the position: rename the deliverable to the new
        # NNN-slug.mp4 (no re-encode), keeping the cache valid.
        if old_rel != rel_path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            (clips_root / old_rel).rename(dest)
        with database.session() as s:
            clip = clips_repo.clip_for_item(s, item_id)
            if clip is not None:
                clips_repo.set_state(s, clip.id, "done", position=position,
                                     filename=filename, rel_path=rel_path)
        logger.info("render: item %s served from cache (%s)", item_id, rel_path)
        return False

    with database.session() as s:
        clip = clips_repo.reset_to_queued(
            s, cut_item_id=item_id, video_id=video_id, cut_id=cut_id,
            position=position, filename=filename, rel_path=rel_path, phash=phash,
            padding_s=margin, profile=profile, start_s=start_s, end_s=end_s,
        )
        clip_id = clip.id

    with database.session() as s:
        clips_repo.set_state(s, clip_id, "downloading_section")

    # The section is ephemeral: temp dir, discarded when the item is done.
    with tempdir_factory(prefix="primocut-clip-") as tmp:
        section = download_section_fn(
            url, Path(tmp), start_s=start_s, end_s=end_s, margin_s=margin,
            duration=duration, settings=settings,
        )
        # `--force-keyframes-at-cuts` makes the section span EXACTLY [dl_start,
        # dl_end], so its local time 0 is the source time `dl_start` — that is the
        # section's origin on the live timeline (the container's own start_time is
        # normalized to ~0 by the merge and carries no source offset). Everything
        # downstream (the align window, the encode trim) offsets from this s0.
        s0, _ = section_mod.section_bounds(start_s, end_s, margin, duration)

        # Surgical WhisperX pass: refine [start, end] from the item window of the
        # section already on disk (never re-downloads, never re-transcribes the
        # live). Any failure silently keeps the original bounds.
        r_start, r_end = start_s, end_s
        if align_enabled:
            with database.session() as s:
                clips_repo.set_state(s, clip_id, "aligning")
            r_start, r_end = refine_bounds_fn(
                section, s0=s0, start_s=start_s, end_s=end_s,
                language=settings.whisper_language, settings=settings,
                workdir=Path(tmp), gpu_lock=gpu_lock,
            )

        local_start = max(0.0, r_start - s0)
        local_dur = r_end - r_start

        with database.session() as s:
            clips_repo.set_state(s, clip_id, "cutting")

        dest.parent.mkdir(parents=True, exist_ok=True)
        encoder_used, warning = encode_clip_fn(
            section, dest, local_start=local_start, local_dur=local_dur,
            profile=profile, settings=settings,
        )

    size = dest.stat().st_size if dest.exists() else 0
    with database.session() as s:
        clips_repo.set_state(
            s, clip_id, "done", encoder_used=encoder_used, size_bytes=size,
            rel_path=rel_path, filename=filename, position=position,
            # Record the EFFECTIVE window actually encoded (refined by the WhisperX
            # pass when it ran); the export SRT/manifest read these, not the request.
            render_start_s=r_start, render_end_s=r_end, duration_s=local_dur,
            error=warning or "",
        )
    logger.info("render: item %s done -> %s (%s, %d bytes)",
                item_id, rel_path, encoder_used, size)
    return True
