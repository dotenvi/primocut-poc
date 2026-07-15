"""Comment synthesis: the content hash, the prompt, quote resolution and the
project-level aggregation of what the audience asked for.

The feature is split by cost (guideline 9). Collecting the comments is
deterministic and quota-accounted, so the snapshot cycle does it automatically
once a video is a definitive outlier (`worker.comment_collection`). Reading them
costs an LLM call, so this pass runs only from an explicit user action.

Two rules shape this module:

- **The model never writes a quote.** The comments reach it indexed (`[i]`) and it
  answers with `example_index`; `resolve_example` maps the index back to the
  stored comment and CLAMPS it. A model that invents an index still cannot invent
  audience words — the same protection `resolve_span` gives timestamps.
- **The aggregation is code, not a second LLM pass.** `request_key` normalises the
  spelling so two videos asking for the same guest merge, and the ranking is
  plain counting — auditable, re-tunable, and free.

The result is persisted in `video_enrichments` (kind=`comment_synthesis`) rather
than `video_attributes`: `metrics.patterns` reports every boolean of an attributes
row as a packaging pattern, and a narrative synthesis has no business there.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

KIND = "comment_synthesis"

# Per-comment cap in the prompt. Comments run long and the tail is rarely the
# point; capping bounds the token spend of a pass we only ever pay for once.
PROMPT_COMMENT_MAX_CHARS = 500

# How many entries of each request type the dashboard reports.
TOP_REQUESTS_PER_TYPE = 20

REQUEST_TYPES = ("guest", "topic", "format", "other")

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CommentRef:
    """A stored comment, detached from the session for use during the LLM call."""

    comment_id: str
    text: str
    like_count: int
    author: str


def to_refs(rows) -> tuple[CommentRef, ...]:
    """Detach DAO rows into plain values (the LLM call holds no session)."""
    return tuple(
        CommentRef(
            comment_id=r.comment_id,
            text=r.text,
            like_count=r.like_count,
            author=r.author,
        )
        for r in rows
    )


# ------------------------- the cache key -------------------------


def comments_content_hash(comments: Sequence[CommentRef]) -> str:
    """SHA-256 over the comment set — the pay-once key of the synthesis.

    Sorted by comment id so the digest depends on the SET, not on the order the
    API happened to return; the like count is included so a re-collection that
    only reshuffles the ranking still counts as new content worth re-reading.
    """
    parts = sorted(f"{c.comment_id}:{c.like_count}:{c.text}" for c in comments)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ------------------------- the prompt -------------------------


def format_comments(comments: Sequence[CommentRef]) -> str:
    """Index the comments for the model: `[i] (N likes) text`, most-liked first."""
    lines = []
    for i, c in enumerate(comments):
        text = _SPACE_RE.sub(" ", c.text).strip()[:PROMPT_COMMENT_MAX_CHARS]
        lines.append(f"[{i}] ({c.like_count} likes) {text}")
    return "\n".join(lines)


def build_user_prompt(title: str, comments: Sequence[CommentRef]) -> str:
    return (
        f"Video title: {title}\n\n"
        f"Top comments ({len(comments)}), most liked first:\n"
        f"{format_comments(comments)}"
    )


# ------------------------- quote resolution (clamped) -------------------------


def resolve_example(comments: Sequence[CommentRef], index) -> dict:
    """Resolve an index the model returned into a real stored comment.

    Out-of-range and non-integer indices are clamped rather than rejected: the
    point is that every quote we display is text the audience actually wrote, and
    a clamped neighbour is honest where a model-authored quote would not be.
    """
    if not comments:
        return {}
    try:
        i = int(index)
    except (TypeError, ValueError):
        i = 0
    i = max(0, min(i, len(comments) - 1))
    c = comments[i]
    return {
        "comment_id": c.comment_id,
        "text": c.text,
        "like_count": c.like_count,
        "author": c.author,
    }


def request_key(value: str) -> str:
    """Normalise a requested name/subject so spelling variants merge.

    Deliberately in code, not in the prompt: the aggregation can be re-tuned
    without re-paying for a single synthesis.
    """
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text.casefold())
    return _SPACE_RE.sub(" ", text).strip()


def build_payload(raw: dict, comments: Sequence[CommentRef]) -> dict:
    """The persisted payload: the model's observations with every index resolved.

    Reads each field defensively — a sloppy or partial response degrades into a
    thinner payload rather than breaking the row.
    """
    themes = []
    for t in raw.get("themes") or []:
        if not isinstance(t, dict):
            continue
        themes.append(
            {
                "theme": str(t.get("theme") or "").strip(),
                "mentions": _int(t.get("mentions")),
                "example": resolve_example(comments, t.get("example_index")),
            }
        )

    requests = []
    for r in raw.get("requests") or []:
        if not isinstance(r, dict):
            continue
        value = str(r.get("value") or "").strip()
        if not value:
            continue
        rtype = r.get("type")
        requests.append(
            {
                "type": rtype if rtype in REQUEST_TYPES else "other",
                "value": value,
                "value_key": request_key(value),
                "mentions": _int(r.get("mentions")),
                "example": resolve_example(comments, r.get("example_index")),
            }
        )

    sentiment = raw.get("sentiment")
    return {
        "themes": themes,
        "requests": requests,
        "sentiment": sentiment if sentiment in
        ("positive", "mixed", "negative", "neutral") else "neutral",
        "sentiment_note": str(raw.get("sentiment_note") or "").strip(),
        "comments_analysed": len(comments),
    }


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ------------------------- project aggregation -------------------------


def aggregate_requests(items: Sequence[tuple[str, dict]]) -> dict:
    """Merge the audience requests of a project's syntheses, grouped by type.

    `items` is (video_id, payload). Requests merge on `request_key`, and the
    reported `value` is the most common raw spelling behind that key.

    Ranked by the number of VIDEOS asking, then by total mentions: several videos
    asking for the same guest is a niche signal, whereas many mentions on a single
    video is one fandom. No minimum-occurrence guard applies here — unlike a
    conditional distribution, this list makes no statistical claim beyond "N
    videos' comments asked for this", which is true at N=1; the count is shown on
    every row so the weight is the reader's to judge.
    """
    # (type, key) -> {"videos": set, "mentions": int, "spellings": {raw: count}}
    acc: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"videos": set(), "mentions": 0, "spellings": defaultdict(int)}
    )
    videos = set()

    for video_id, payload in items:
        videos.add(video_id)
        for r in (payload or {}).get("requests") or []:
            if not isinstance(r, dict):
                continue
            value = str(r.get("value") or "").strip()
            if not value:
                continue
            key = r.get("value_key") or request_key(value)
            rtype = r.get("type") if r.get("type") in REQUEST_TYPES else "other"
            entry = acc[(rtype, key)]
            entry["videos"].add(video_id)
            entry["mentions"] += _int(r.get("mentions"))
            entry["spellings"][value] += 1

    by_type: dict[str, list[dict]] = {t: [] for t in REQUEST_TYPES}
    for (rtype, _key), entry in acc.items():
        spellings = entry["spellings"]
        by_type[rtype].append(
            {
                "value": max(spellings, key=lambda v: (spellings[v], -len(v))),
                "videos": len(entry["videos"]),
                "mentions": entry["mentions"],
                "video_ids": sorted(entry["videos"]),
            }
        )
    for rtype, rows in by_type.items():
        rows.sort(key=lambda r: (-r["videos"], -r["mentions"], r["value"]))
        by_type[rtype] = rows[:TOP_REQUESTS_PER_TYPE]

    return {"videos_synthesised": len(videos), "by_type": by_type}
