"""Opt-in AI enrichment: thumbnail (vision), title (semantics), comments (synthesis).

Costs an LLM call, so it NEVER runs on a schedule — only on an explicit user
action (guideline 9). Every result is keyed by a **content hash**, so it is paid
for once and shared across every project that tracks the channel:

| kind        | stored in           | content hash                        | recomputed when       |
|-------------|---------------------|-------------------------------------|-----------------------|
| `title`     | `video_attributes`  | sha256 of the title                 | the title changes     |
| `thumbnail` | `video_attributes`  | `competitor_videos.thumbnail_hash`  | the image BYTES change|
| `comments`  | `video_enrichments` | sha256 of the collected comment set | the comments change   |

The thumbnail hash is the byte hash the snapshot cycle already maintains (the URL
is not trusted), so checking the cache costs no network at all — bytes are fetched
only once the hash says the work is due. The comment set is likewise already in
the database (collected automatically), so the gate is a pure read there too.

The two packaging rows store the raw tool output PLUS boolean facets derived here
in code. `metrics.patterns` only counts booleans, so the facets are what reaches
the conditional distributions; deriving them here (rather than asking the model
for them) keeps every threshold — 2 faces, 4 words — auditable and tunable without
re-paying for the analysis. The comment synthesis deliberately lands in a
different table for the mirror-image reason: it is narrative, and must NOT be
counted as a packaging pattern. No score is ever stored (guideline 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..analysis.enrichment_prompts import (
    COMMENT_SYNTHESIS_SYSTEM,
    COMMENT_SYNTHESIS_TOOL,
    THUMBNAIL_SYSTEM,
    THUMBNAIL_TOOL,
    TITLE_SYSTEM,
    TITLE_TOOL,
)
from ..config import Settings, get_settings
from ..db import competitors as crepo
from ..youtube.thumbnails import ThumbnailFetcher, fetch_thumbnail_bytes
from . import comments as mcomments
from .attributes import title_content_hash

KIND_TITLE = "title"
KIND_THUMBNAIL = "thumbnail"
KIND_COMMENTS = "comments"

SOURCE_TITLE = "title_llm"
SOURCE_THUMBNAIL = "thumbnail_llm"
# The comment synthesis is keyed by `video_enrichments.kind`, not by an attributes
# source — a different store, for the reason in the module docstring.
SOURCE_COMMENTS = mcomments.KIND

_SOURCES = {
    KIND_TITLE: SOURCE_TITLE,
    KIND_THUMBNAIL: SOURCE_THUMBNAIL,
    KIND_COMMENTS: SOURCE_COMMENTS,
}

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

# Thresholds for the derived facets (kept here, not in the prompt, so they can
# change without re-paying for the analysis).
MULTIPLE_FACES = 2
TEXT_HEAVY_WORDS = 4

_EXPRESSIONS = ("none", "neutral", "happy", "shock", "anger", "sadness", "fear", "disgust")
_COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "pink", "brown",
           "black", "white", "gray")
_TONES = ("neutral", "hype", "outrage", "humor", "curiosity", "drama", "informative")

# Magic bytes → media type. YouTube serves JPEG or WebP depending on the size.
_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
)


def source_for(kind: str) -> str:
    """Map a user-facing kind to the storage key its result is written under."""
    try:
        return _SOURCES[kind]
    except KeyError:
        raise ValueError(
            f"unknown enrichment kind: {kind!r} "
            f"(use 'thumbnail', 'title' or 'comments')"
        ) from None


def validate_kind(kind: str, *, regenerate: bool = False) -> str:
    """Check a caller's request up front, returning the storage key.

    Rejecting `regenerate` for the packaging kinds rather than ignoring it: they
    have nothing to re-fetch (their content is collected by the snapshot cycle),
    so the flag can only mean the caller expected something we would not do.
    """
    source = source_for(kind)
    if regenerate and kind != KIND_COMMENTS:
        raise ValueError(
            f"regenerate applies only to the 'comments' kind, not {kind!r} — "
            f"packaging is re-analysed automatically when its content hash changes"
        )
    return source


def is_synthesis(source: str) -> bool:
    """Whether a storage key lives in `video_enrichments` (vs `video_attributes`)."""
    return source == SOURCE_COMMENTS


@dataclass(frozen=True)
class Plan:
    """A due enrichment: everything the LLM call needs, read before any network."""

    video_id: str
    kind: str
    source: str
    content_hash: str
    title: str
    thumbnail_url: str = ""
    comments: tuple[mcomments.CommentRef, ...] = field(default_factory=tuple)


# ------------------------- derived boolean facets -------------------------


def derive_thumbnail_facets(raw: dict) -> dict:
    """Boolean facets of a thumbnail analysis, for the conditional distributions.

    The enums are one-hot expanded in full (not just the observed value) so the
    reported attribute set does not depend on which videos happen to be enriched.
    """
    faces = int(raw.get("face_count") or 0)
    words = int(raw.get("text_word_count") or 0)
    expression = raw.get("dominant_expression") or "none"
    colors = {c for c in (raw.get("dominant_colors") or []) if isinstance(c, str)}

    facets: dict[str, bool] = {
        "thumb_has_face": faces >= 1,
        "thumb_multiple_faces": faces >= MULTIPLE_FACES,
        "thumb_has_text": bool(raw.get("has_text")),
        "thumb_text_heavy": words >= TEXT_HEAVY_WORDS,
        "thumb_high_contrast": raw.get("contrast") == "high",
        "thumb_has_arrow": bool(raw.get("has_arrow")),
        "thumb_has_circle": bool(raw.get("has_circle_or_highlight")),
        "thumb_confrontation": bool(raw.get("is_confrontation_framing")),
    }
    facets.update({f"thumb_expression_{e}": expression == e for e in _EXPRESSIONS})
    facets.update({f"thumb_color_{c}": c in colors for c in _COLORS})
    return facets


def derive_title_facets(raw: dict) -> dict:
    """Boolean facets of a title analysis, for the conditional distributions."""
    names = [n for n in (raw.get("person_names") or []) if isinstance(n, str) and n.strip()]
    tone = raw.get("tone") or "neutral"

    facets: dict[str, bool] = {
        "title_has_person_name": len(names) >= 1,
        "title_multiple_people": len(names) >= 2,
        "title_has_quoted_speech": bool((raw.get("quoted_speech") or "").strip()),
    }
    facets.update({f"title_tone_{t}": tone == t for t in _TONES})
    return facets


def _facets(kind: str, raw: dict) -> dict:
    return derive_title_facets(raw) if kind == KIND_TITLE else derive_thumbnail_facets(raw)


# ------------------------- plan / run / save -------------------------


def plan_enrichment(session, video_id: str, kind: str) -> Plan | None:
    """What to enrich, or None when the cache already covers it.

    Raises KeyError for an unknown video and ValueError when the content cannot be
    hashed (a thumbnail whose bytes never downloaded, a video whose comments were
    never collected): without a cache key every click would recharge, so we refuse
    instead of paying repeatedly.
    """
    source = source_for(kind)
    video = crepo.get_video(session, video_id)
    if video is None:
        raise KeyError(video_id)

    comments: tuple[mcomments.CommentRef, ...] = ()
    if kind == KIND_TITLE:
        content_hash = title_content_hash(video.title)
    elif kind == KIND_THUMBNAIL:
        content_hash = video.thumbnail_hash
        if not content_hash:
            raise ValueError(
                "thumbnail bytes have not been hashed for this video yet — "
                "nothing to key the cache on; run a snapshot cycle first"
            )
    else:
        comments = mcomments.to_refs(crepo.list_video_comments(session, video_id))
        if not comments:
            raise ValueError(_no_comments_reason(video))
        content_hash = mcomments.comments_content_hash(comments)

    existing = (
        crepo.get_video_enrichment(session, video_id, source)
        if is_synthesis(source)
        else crepo.get_video_attributes(session, video_id, source)
    )
    if (
        existing is not None
        and existing.state == STATE_DONE
        and existing.content_hash
        and existing.content_hash == content_hash
    ):
        return None  # content unchanged — already paid for

    return Plan(
        video_id=video_id, kind=kind, source=source, content_hash=content_hash,
        title=video.title, thumbnail_url=video.thumbnail_url, comments=comments,
    )


def _no_comments_reason(video) -> str:
    """Say WHY there is nothing to synthesise — the UI renders this verbatim."""
    if video.comments_state == "disabled":
        return "comments are disabled on this video, so there is nothing to synthesise"
    if video.comments_state == "error":
        return (
            f"the last comment collection failed ({video.comments_error}) — "
            f"it is retried on the next snapshot cycle"
        )
    if video.comments_state == "done":
        # Collected successfully and the thread was simply empty.
        return "this video has no comments, so there is nothing to synthesise"
    return (
        "no comments have been collected for this video yet — they are collected "
        "automatically once it becomes a definitive outlier"
    )


def _media_type(data: bytes) -> str:
    for magic, media_type in _MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"  # YouTube's default; a wrong guess is the provider's error to raise


def run_enrichment(
    plan: Plan,
    *,
    llm: Any,
    settings: Settings | None = None,
    thumbnail_fetcher: ThumbnailFetcher = fetch_thumbnail_bytes,
) -> dict:
    """Perform the paid call. Network only — no session, no transaction held."""
    settings = settings or get_settings()

    if plan.kind == KIND_TITLE:
        return llm.select(
            system=TITLE_SYSTEM,
            user=f"Video title: {plan.title}",
            tool=TITLE_TOOL,
            tool_name=TITLE_TOOL["name"],
            model=settings.enrich_title_model or None,
        )

    if plan.kind == KIND_COMMENTS:
        return llm.select(
            system=COMMENT_SYNTHESIS_SYSTEM,
            user=mcomments.build_user_prompt(plan.title, plan.comments),
            tool=COMMENT_SYNTHESIS_TOOL,
            tool_name=COMMENT_SYNTHESIS_TOOL["name"],
            model=settings.enrich_comments_model or None,
        )

    data = thumbnail_fetcher(plan.thumbnail_url)
    return llm.select(
        system=THUMBNAIL_SYSTEM,
        user="Analyse this YouTube thumbnail.",
        tool=THUMBNAIL_TOOL,
        tool_name=THUMBNAIL_TOOL["name"],
        images=[(_media_type(data), data)],
        model=settings.enrich_vision_model or None,
    )


def save_enrichment(session, plan: Plan, raw: dict):
    """Persist the model's output, marking the row done.

    The packaging kinds store the raw tool output merged with the facets derived
    in code; the synthesis stores the payload with every quote resolved back to a
    real stored comment.
    """
    if plan.kind == KIND_COMMENTS:
        return crepo.upsert_video_enrichment(
            session,
            video_id=plan.video_id,
            kind=plan.source,
            payload=mcomments.build_payload(raw, plan.comments),
            content_hash=plan.content_hash,
            state=STATE_DONE,
            error="",
        )
    return crepo.upsert_video_attributes(
        session,
        video_id=plan.video_id,
        source=plan.source,
        attributes={**raw, **_facets(plan.kind, raw)},
        content_hash=plan.content_hash,
        state=STATE_DONE,
        error="",
    )


def mark_running(session, video_id: str, source: str) -> None:
    """Flag the row as in flight so the UI can show it (polled like a backfill)."""
    if is_synthesis(source):
        crepo.set_video_enrichment_state(session, video_id, source, state=STATE_RUNNING)
    else:
        crepo.set_video_attributes_state(session, video_id, source, state=STATE_RUNNING)


def mark_error(session, video_id: str, source: str, message: str) -> None:
    """Record a visible failure. The content hash is cleared so the next explicit
    click retries this video instead of treating the failure as cached."""
    if is_synthesis(source):
        crepo.set_video_enrichment_state(
            session, video_id, source, state=STATE_ERROR, error=message, clear_hash=True
        )
    else:
        crepo.set_video_attributes_state(
            session, video_id, source, state=STATE_ERROR, error=message, clear_hash=True
        )


# The enrichment writes RAW attributes only. It is deliberately absent from the
# snapshot cycle and from `metrics refresh`: both are free and scheduled, this is
# paid and explicit (guideline 9).
