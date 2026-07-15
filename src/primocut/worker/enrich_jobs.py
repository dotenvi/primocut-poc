"""RQ job: AI packaging enrichment of a set of videos (opt-in — costs LLM).

Runs ONLY from an explicit user action — the video-detail button or the project
batch (guideline 9). The single-video case is just a one-element list, so there is
one code path, one isolation story and one thing for the UI to poll.

Resilience (DEVELOPMENT.md §4): per-video isolation, exactly like the snapshot
cycle. A video that fails is logged, marked `state="error"` with its message (which
is what the UI renders), and the batch moves on — one bad thumbnail never wastes
the whole run. Network happens outside the write transactions.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..metrics import enrichment
from ..youtube.thumbnails import ThumbnailFetcher, fetch_thumbnail_bytes

logger = get_logger(__name__)


def enrich_videos(
    video_ids: Iterable[str],
    kind: str,
    *,
    settings: Settings | None = None,
    database=None,
    llm: Any | None = None,
    thumbnail_fetcher: ThumbnailFetcher = fetch_thumbnail_bytes,
) -> dict:
    """Enrich each video's `kind` packaging, skipping whatever the cache covers.

    Raises ValueError for an unknown `kind` (a caller bug, not a per-video
    failure); every other error is contained to its own video.
    """
    source = enrichment.source_for(kind)  # fail fast: a bad kind is not a video error
    settings = settings or get_settings()
    if database is None:
        from ..db import get_database

        database = get_database(settings)
    if llm is None:
        from ..analysis.llm import get_llm

        llm = get_llm(settings)

    enriched = skipped = failed = 0
    for video_id in video_ids:
        try:
            with database.session() as s:
                plan = enrichment.plan_enrichment(s, video_id, kind)
            if plan is None:
                skipped += 1  # content unchanged — already paid for
                continue

            with database.session() as s:
                enrichment.mark_running(s, video_id, source)

            # Paid + network work, deliberately outside any transaction.
            raw = enrichment.run_enrichment(
                plan, llm=llm, settings=settings, thumbnail_fetcher=thumbnail_fetcher
            )

            with database.session() as s:
                enrichment.save_enrichment(s, plan, raw)
            enriched += 1
        except Exception as exc:  # per-video isolation: log, record, keep going
            logger.error("enrich(%s): video %s failed: %s", kind, video_id, exc)
            failed += 1
            try:
                with database.session() as s:
                    enrichment.mark_error(s, video_id, source, str(exc))
            except Exception as inner:  # a video we cannot even flag must not abort the batch
                logger.error("enrich(%s): could not record the failure of %s: %s",
                             kind, video_id, inner)
            continue

    logger.info(
        "enrich(%s) done: %d enriched, %d cached, %d failed", kind, enriched, skipped, failed
    )
    return {"kind": kind, "enriched": enriched, "skipped": skipped, "failed": failed}
