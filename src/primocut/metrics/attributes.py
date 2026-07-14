"""Syntactic title attributes (deterministic, no LLM) + persistence.

The features are extracted with regex/heuristics from the video title and stored
in `video_attributes` (source="syntactic"), keyed by a content hash of the title
so a row is recomputed only when the title actually changes (and can be shared
across projects). These raw attributes feed the per-project conditional
distributions in `metrics.patterns`; no score is stored here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from ..db import competitors as crepo

SOURCE = "syntactic"

# Any character outside the Basic Multilingual Plan's common ranges — a cheap,
# dependency-free proxy for emoji/pictographs in a title.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # letter-only tokens


def syntactic_attributes(title: str) -> dict:
    """Extract deterministic packaging features from a title."""
    text = title or ""
    words = _WORD_RE.findall(text)
    caps_words = [w for w in words if len(w) >= 2 and w.upper() == w]
    caps_ratio = (len(caps_words) / len(words)) if words else 0.0
    return {
        "length": len(text),
        "word_count": len(words),
        "caps_ratio": round(caps_ratio, 4),
        "caps_heavy": caps_ratio >= 0.5 and len(words) >= 2,
        "has_digit": any(ch.isdigit() for ch in text),
        "has_question": "?" in text,
        "has_exclamation": "!" in text,
        "has_quote": any(q in text for q in ('"', "“", "”", "'", "‘", "’")),
        "has_brackets_or_pipe": any(ch in text for ch in "[]()|"),
        "has_emoji": bool(_EMOJI_RE.search(text)),
    }


def title_content_hash(title: str) -> str:
    return hashlib.sha256((title or "").encode("utf-8")).hexdigest()


def refresh_syntactic_attributes(session, video_ids: Iterable[str]) -> int:
    """Upsert the syntactic attributes for the given videos, recomputing only when
    the title's content hash changed. Returns the number of rows written."""
    written = 0
    for video_id in video_ids:
        video = crepo.get_video(session, video_id)
        if video is None:
            continue
        content_hash = title_content_hash(video.title)
        existing = crepo.get_video_attributes(session, video_id, SOURCE)
        if existing is not None and existing.content_hash == content_hash:
            continue  # title unchanged — nothing to recompute
        crepo.upsert_video_attributes(
            session,
            video_id=video_id,
            source=SOURCE,
            attributes=syntactic_attributes(video.title),
            content_hash=content_hash,
        )
        written += 1
    return written
