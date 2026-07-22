"""RQ jobs / CLI cores for the Phase 3 AI packaging of a cut (opt-in — guideline 9).

Four actions, all triggered ONLY by an explicit user action:

- `generate_cut_packaging` (kind `title`/`thumb_brief`): one LLM pass PER clip,
  driven by the LIVE's project patterns (`packaging_signals`). Per-item isolation
  (a failed clip never wastes the others) + a pay-once content-hash cache.
- `extract_cut_frames`: DETERMINISTIC frame extraction (FFmpeg only, NO LLM); pay
  once by presence. Candidate frames land under the clip's frames dir.
- `rank_cut_frames`: the SEPARATE, opt-in vision pass that scores the extracted
  frames against the brief. Runs only when explicitly triggered.
- `match_live_audience`: the cheap, cached-per-live semantic match of the niche's
  audience requests to the live's topics (the deferred `audience_asked` signal).
  Degrades silently to an empty match when the niche has no syntheses.

Context is always built from `video.project_id`, so a channel tracked in two
projects contributes the patterns of the LIVE's project, never the other's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..config import Settings, get_settings
from ..db import cuts as cuts_repo
from ..db import packaging as pkg_repo
from ..db import repo
from ..logging_conf import get_logger
from ..metrics import packaging_signals
from ..packaging import generate as gen
from ..render import frames as frames_mod

logger = get_logger(__name__)

_LLM_KINDS = ("title", "thumb_brief")


# ------------------------- clip content -------------------------


def _excerpt(segments, start: float, end: float, *, limit: int = 1200) -> str:
    """The transcript text overlapping [start, end], truncated to `limit` chars."""
    parts: list[str] = []
    for seg in segments:
        s = float(seg["start"] if isinstance(seg, dict) else seg.start)
        e = float(seg["end"] if isinstance(seg, dict) else seg.end)
        text = str((seg["text"] if isinstance(seg, dict) else seg.text) or "").strip()
        if e <= start or s >= end or not text:
            continue
        parts.append(text)
        if sum(len(p) for p in parts) >= limit:
            break
    return " ".join(parts)[:limit]


def _clip_content(s, item, segments) -> dict:
    """Title/summary/transcript excerpt for one cut item (topic or manual range)."""
    title, summary = "Manual range", ""
    if item.topic_id is not None:
        from ..db.models import TopicRow

        topic = s.get(TopicRow, item.topic_id)
        if topic is not None:
            title, summary = topic.title, topic.summary
    return {
        "title": title, "summary": summary,
        "transcript_excerpt": _excerpt(segments, item.start_s, item.end_s),
    }


def _plan_items(s, video_id, cut_id, item_ids):
    """Snapshot the (item, clip-content) work list up front, outside the LLM call."""
    transcript = repo.get_transcript(s, video_id)
    segments = list(transcript.segments) if transcript else []
    wanted = set(item_ids) if item_ids is not None else None
    out = []
    for it in cuts_repo.list_items(s, cut_id):
        if wanted is not None and it.id not in wanted:
            continue
        out.append({"item_id": it.id, "position": it.position,
                    "clip": _clip_content(s, it, segments)})
    return out


# ------------------------- title / brief generation -------------------------


def generate_cut_packaging(
    video_id: str,
    cut_id: int,
    kind: str,
    *,
    item_ids: Iterable[int] | None = None,
    k: int | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    llm: Any | None = None,
) -> dict:
    """Generate `kind` (`title`/`thumb_brief`) for a cut's items, per-item isolated."""
    if kind not in _LLM_KINDS:
        raise ValueError(f"unknown packaging kind: {kind!r} (use 'title' or 'thumb_brief')")
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if llm is None:
        from ..analysis.llm import get_llm

        llm = get_llm(settings)
    k = k or settings.packaging_titles_k

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise ValueError(f"cut {cut_id} not found in live {video_id!r}")
        video = repo.get_video(s, video_id)
        context = packaging_signals.build_packaging_context(
            s, video.project_id, settings=settings)
        jobs = _plan_items(s, video_id, cut_id, item_ids)

    if context is None:  # live's project vanished — nothing to ground on
        raise ValueError("the live's project has no packaging context")

    generated = skipped = failed = 0
    for j in jobs:
        item_id, clip = j["item_id"], j["clip"]
        chash = gen.content_hash(kind, clip, context, k=k if kind == "title" else 0)
        try:
            with database.session() as s:
                existing = pkg_repo.get_packaging(s, item_id, kind)
                if (not regenerate and existing is not None
                        and existing.state == "done"
                        and existing.content_hash == chash):
                    skipped += 1
                    continue
                pkg_repo.upsert_packaging(
                    s, cut_item_id=item_id, cut_id=cut_id, kind=kind,
                    payload={}, content_hash=chash, state="running", error="")

            # The paid call, outside any transaction.
            if kind == "title":
                payload = gen.generate_titles(clip, context, k, llm=llm, settings=settings)
            else:
                payload = gen.generate_brief(clip, context, llm=llm, settings=settings)

            with database.session() as s:
                pkg_repo.upsert_packaging(
                    s, cut_item_id=item_id, cut_id=cut_id, kind=kind,
                    payload=payload, content_hash=chash, state="done", error="")
            generated += 1
        except Exception as exc:  # per-item isolation
            logger.error("packaging(%s): item %s of cut %s failed: %s",
                         kind, item_id, cut_id, exc)
            failed += 1
            try:
                with database.session() as s:
                    pkg_repo.set_packaging_state(s, item_id, kind, "error", error=str(exc))
            except Exception as inner:
                logger.error("packaging(%s): could not record failure of %s: %s",
                             kind, item_id, inner)
            continue

    logger.info("packaging(%s, cut %s): %d generated, %d cached, %d failed",
                kind, cut_id, generated, skipped, failed)
    return {"cut_id": cut_id, "kind": kind, "generated": generated,
            "skipped": skipped, "failed": failed}


# ------------------------- frame extraction (deterministic, NO LLM) -------------------------


def frames_dir_for(settings: Settings, video_id: str, cut_id: int, position: int) -> Path:
    """Where a clip's candidate frames live (beside the clips, under a frames dir)."""
    return Path(settings.clips_dir) / video_id / str(cut_id) / "frames" / f"{position:03d}"


def extract_cut_frames(
    video_id: str,
    cut_id: int,
    *,
    item_ids: Iterable[int] | None = None,
    count: int | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    extract_fn=frames_mod.extract_frames,
) -> dict:
    """Extract candidate frames for a cut's rendered items — FFmpeg only, no LLM."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    count = count or settings.packaging_frames_per_clip
    clips_root = Path(settings.clips_dir)

    from ..db import clips as clips_repo

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise ValueError(f"cut {cut_id} not found in live {video_id!r}")
        clips = clips_repo.clips_by_item(s, cut_id)
        wanted = set(item_ids) if item_ids is not None else None
        jobs = []
        for it in cuts_repo.list_items(s, cut_id):
            if wanted is not None and it.id not in wanted:
                continue
            clip = clips.get(it.id)
            if clip is not None and clip.state == "done" and clip.rel_path:
                jobs.append({"item_id": it.id, "position": it.position,
                             "rel_path": clip.rel_path})

    extracted = failed = 0
    for j in jobs:
        try:
            out_dir = frames_dir_for(settings, video_id, cut_id, j["position"])
            extract_fn(clips_root / j["rel_path"], out_dir, count=count,
                       scene_threshold=settings.packaging_scene_threshold,
                       regenerate=regenerate)
            extracted += 1
        except Exception as exc:  # per-item isolation
            logger.error("frames: item %s of cut %s failed: %s", j["item_id"], cut_id, exc)
            failed += 1
            continue

    logger.info("frames(cut %s): %d extracted, %d failed", cut_id, extracted, failed)
    return {"cut_id": cut_id, "extracted": extracted, "failed": failed}


# ------------------------- frame ranking (opt-in, vision) -------------------------


def _load_frame_images(frames_dir: Path, names: list[str]) -> list[tuple[str, bytes]]:
    out = []
    for name in names:
        data = (frames_dir / name).read_bytes()
        media = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        out.append((media, data))
    return out


def rank_cut_frames(
    video_id: str,
    cut_id: int,
    *,
    item_ids: Iterable[int] | None = None,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    llm: Any | None = None,
    image_loader=_load_frame_images,
) -> dict:
    """Rank each item's extracted frames against its brief (opt-in vision pass)."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if llm is None:
        from ..analysis.llm import get_llm

        llm = get_llm(settings)

    with database.session() as s:
        cut = cuts_repo.get_cut(s, video_id, cut_id)
        if cut is None:
            raise ValueError(f"cut {cut_id} not found in live {video_id!r}")
        by_item = pkg_repo.packaging_by_item(s, cut_id)
        wanted = set(item_ids) if item_ids is not None else None
        jobs = []
        for it in cuts_repo.list_items(s, cut_id):
            if wanted is not None and it.id not in wanted:
                continue
            brief_row = by_item.get(it.id, {}).get("thumb_brief")
            jobs.append({"item_id": it.id, "position": it.position,
                         "brief": brief_row.payload if brief_row is not None else None})

    ranked = skipped = failed = 0
    for j in jobs:
        item_id = j["item_id"]
        frames_dir = frames_dir_for(settings, video_id, cut_id, j["position"])
        names = sorted(p.name for p in frames_dir.glob("frame-*.jpg"))
        if not names:
            skipped += 1  # nothing extracted for this item yet
            continue
        try:
            with database.session() as s:
                pkg_repo.upsert_packaging(
                    s, cut_item_id=item_id, cut_id=cut_id, kind="frame_ranking",
                    payload={}, content_hash="", state="running", error="")
            images = image_loader(frames_dir, names)
            payload = gen.rank_frames(names, j["brief"], images, llm=llm, settings=settings)
            with database.session() as s:
                pkg_repo.upsert_packaging(
                    s, cut_item_id=item_id, cut_id=cut_id, kind="frame_ranking",
                    payload=payload, content_hash="", state="done", error="")
            ranked += 1
        except Exception as exc:  # per-item isolation
            logger.error("frame_ranking: item %s of cut %s failed: %s",
                         item_id, cut_id, exc)
            failed += 1
            try:
                with database.session() as s:
                    pkg_repo.set_packaging_state(s, item_id, "frame_ranking", "error",
                                                 error=str(exc))
            except Exception:
                pass
            continue

    logger.info("frame_ranking(cut %s): %d ranked, %d skipped, %d failed",
                cut_id, ranked, skipped, failed)
    return {"cut_id": cut_id, "ranked": ranked, "skipped": skipped, "failed": failed}


# ------------------------- audience match (cheap, cached per live) -------------------------


def _audience_requests(session, project_id, *, settings) -> list[dict]:
    """The niche's ranked audience requests, flattened across request types."""
    from ..metrics import comments as mcomments
    from ..db import competitors as crepo

    agg = mcomments.aggregate_requests([
        (row.video_id, row.payload)
        for row in crepo.list_project_enrichments(session, project_id, mcomments.KIND)
    ])
    out: list[dict] = []
    for rtype, rows in agg.get("by_type", {}).items():
        for r in rows:
            out.append({"type": rtype, "value": r["value"], "videos": r["videos"]})
    return out


def match_live_audience(
    video_id: str,
    *,
    regenerate: bool = False,
    settings: Settings | None = None,
    database=None,
    llm: Any | None = None,
) -> dict:
    """Match the niche's audience requests to the live's topics, cached per live."""
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)

    with database.session() as s:
        video = repo.get_video(s, video_id)
        if video is None:
            raise KeyError(video_id)
        topic_rows = repo.get_topic_rows(s, video_id)
        topics = [{"id": t.id, "title": t.title, "summary": t.summary} for t in topic_rows]
        requests = _audience_requests(s, video.project_id, settings=settings)
        existing = pkg_repo.get_audience_match(s, video_id)

    # The cache key folds the live's topics AND the niche's request set, so the
    # match is recomputed only when either moves.
    import hashlib

    fingerprint = gen._stable({
        "topics": [(t["id"], t["title"]) for t in topics],
        "requests": [(r["type"], r["value"]) for r in requests],
    })
    chash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    if (not regenerate and existing is not None and existing.state == "done"
            and existing.content_hash == chash):
        return {"video_id": video_id, "state": "cached",
                "matched": len(existing.payload or {})}

    if llm is None:
        from ..analysis.llm import get_llm

        llm = get_llm(settings)

    # Mark it running so the bench can poll an in-flight match (mirrors packaging).
    with database.session() as s:
        pkg_repo.upsert_audience_match(s, video_id=video_id, payload={},
                                       content_hash="", state="running", error="")
    try:
        payload = gen.match_audience(topics, requests, llm=llm, settings=settings)
        with database.session() as s:
            pkg_repo.upsert_audience_match(s, video_id=video_id, payload=payload,
                                           content_hash=chash, state="done", error="")
        return {"video_id": video_id, "state": "done", "matched": len(payload)}
    except Exception as exc:
        logger.error("audience_match(%s) failed: %s", video_id, exc)
        with database.session() as s:
            pkg_repo.upsert_audience_match(s, video_id=video_id, payload={},
                                           content_hash="", state="error", error=str(exc))
        raise
