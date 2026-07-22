"""Pure LLM passes for the Phase 3 AI packaging (no DB, no network but the model).

Each function builds a prompt from a `metrics.packaging_signals` context + the
clip's content, forces the matching tool, and VALIDATES the answer against what was
measured before returning it:

- titles: exactly K proposals; every cited pattern must resolve to a measured one
  (an unmatched citation is dropped — the never-invent rule for patterns), and
  `length_chars` is recomputed in code, never trusted from the model.
- brief: the structured brief, with references clamped to real outlier video ids.
- frame ranking: the model returns frame INDICES, resolved/clamped to a really
  extracted frame (the golden rule, applied to stills).
- audience match: topic ids clamped to the live's real topics; a live with no niche
  requests returns an empty match WITHOUT calling the model (silent degradation).

The cache gate + persistence live in the worker (`worker.packaging_jobs`), the same
split `metrics.enrichment` uses. Everything is multi-provider via `SelectionLLM`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from ..analysis import packaging_prompts as pp
from ..config import Settings, get_settings


# ------------------------- content hashing (pay-once keys) -------------------------


def _stable(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _patterns_fingerprint(context: dict) -> str:
    """A stable fingerprint of the measured patterns that shaped a generation.

    Only the measured signal — not volatile metadata — so a title set is reused
    while the niche patterns are unchanged and recomputed when they move.
    """
    return _stable({
        "differential": context.get("differential", []),
        "conventions": context.get("conventions", []),
        "carries_value": context.get("carries_value", {}),
        "examples": [e.get("video_id") for e in context.get("examples", [])],
        "flat": context.get("flat", False),
    })


def content_hash(kind: str, clip: dict, context: dict, *, k: int = 0) -> str:
    """Pay-once key over the clip content + the niche patterns (+ K for titles)."""
    raw = _stable({
        "kind": kind, "k": k,
        "clip": {"title": clip.get("title", ""), "summary": clip.get("summary", ""),
                 "excerpt": clip.get("transcript_excerpt", "")},
        "patterns": _patterns_fingerprint(context),
    })
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------- titles -------------------------


def generate_titles(
    clip: dict, context: dict, k: int, *, llm: Any, settings: Settings | None = None,
) -> dict:
    """Propose K titles for the clip, each citing measured patterns."""
    settings = settings or get_settings()
    ids = pp.pattern_index(context)
    tool = pp.titles_tool(k)
    raw = llm.select(
        system=pp.TITLES_SYSTEM,
        user=pp.build_titles_prompt(clip, context, k, ids),
        tool=tool,
        tool_name=tool["name"],
        model=settings.packaging_titles_model or None,
    )
    titles = _clean_titles(raw.get("titles") or [], ids, k)
    return {"titles": titles, "flat": bool(context.get("flat"))}


def _clean_titles(raw_titles: list, ids: dict, k: int) -> list[dict]:
    """Keep at most K titles; drop citations that name no measured pattern."""
    cleaned: list[dict] = []
    for t in raw_titles[:k]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        applied = []
        for c in t.get("patterns_applied") or []:
            ref = (c or {}).get("reference")
            if ref in ids:  # only citations that resolve to a measured pattern
                applied.append({"kind": ids[ref]["kind"], "reference": ref})
        cleaned.append({
            "title": title,
            "length_chars": len(title),  # recomputed, never trusted
            "patterns_applied": applied,
            "rationale": str(t.get("rationale") or "").strip(),
        })
    return cleaned


# ------------------------- thumbnail brief -------------------------


def generate_brief(
    clip: dict, context: dict, *, llm: Any, settings: Settings | None = None,
) -> dict:
    """Produce the structured thumbnail brief (no image)."""
    settings = settings or get_settings()
    ids = pp.pattern_index(context)
    raw = llm.select(
        system=pp.BRIEF_SYSTEM,
        user=pp.build_brief_prompt(clip, context, ids),
        tool=pp.BRIEF_TOOL,
        tool_name=pp.BRIEF_TOOL["name"],
        model=settings.packaging_brief_model or None,
    )
    # Clamp references to real outlier examples — the model cannot cite a video the
    # niche never surfaced.
    example_ids = {e["video_id"] for e in context.get("examples", [])}
    refs = [r for r in (raw.get("references") or [])
            if isinstance(r, dict) and r.get("video_id") in example_ids]
    raw["references"] = refs
    raw["flat"] = bool(context.get("flat"))
    return raw


# ------------------------- frame ranking (opt-in, vision) -------------------------


def rank_frames(
    frame_names: Sequence[str],
    brief: dict | None,
    images: Sequence[tuple[str, bytes]],
    *,
    llm: Any,
    settings: Settings | None = None,
) -> dict:
    """Rank already-extracted frames against the brief. Indices resolve to files.

    `images` are the frame bytes in the SAME order as `frame_names`. The model
    returns frame indices; we resolve/clamp them to a real file so it can never
    point at a frame that was not extracted.
    """
    settings = settings or get_settings()
    n = len(frame_names)
    brief_text = _brief_summary(brief)
    user = (f"Thumbnail brief:\n{brief_text}\n\n"
            f"Rank the {n} candidate frames [0..{n - 1}] against the brief.")
    raw = llm.select(
        system=pp.FRAME_RANK_SYSTEM,
        user=user,
        tool=pp.FRAME_RANK_TOOL,
        tool_name=pp.FRAME_RANK_TOOL["name"],
        images=list(images),
        model=settings.packaging_frame_rank_model or None,
    )
    rankings = []
    for r in raw.get("rankings") or []:
        idx = _clamp_index(r.get("frame"), n)
        if idx is None:
            continue
        rankings.append({
            "frame": idx, "filename": frame_names[idx],
            "score": float(r.get("score") or 0.0),
            "expression": float(r.get("expression") or 0.0),
            "sharpness": float(r.get("sharpness") or 0.0),
            "framing": float(r.get("framing") or 0.0),
            "fits_brief": bool(r.get("fits_brief")),
            "note": str(r.get("note") or ""),
        })
    best = _clamp_index(raw.get("best_frame"), n)
    if best is None:
        # Fall back to the highest-scored ranking, or the first frame.
        best = (max(rankings, key=lambda r: r["score"])["frame"] if rankings else 0)
    return {"rankings": rankings, "best_frame": best,
            "best_filename": frame_names[best] if n else ""}


def _clamp_index(value: Any, n: int) -> int | None:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    return max(0, min(n - 1, idx))


def _brief_summary(brief: dict | None) -> str:
    if not brief:
        return "(no brief generated — judge on general thumbnail quality)"
    comp = brief.get("composition", {})
    text = brief.get("text", {})
    return (f"faces={comp.get('face_count')}, expression={comp.get('expression')}, "
            f"framing={comp.get('framing')}, text≈{text.get('word_count')} words "
            f"({text.get('suggestion', '')!r})")


# ------------------------- audience match (cheap, cached per live) -------------------------


def match_audience(
    topics: Sequence[dict], requests: Sequence[dict], *, llm: Any,
    settings: Settings | None = None,
) -> dict:
    """Match the niche's audience requests to the live's topics.

    Returns {topic_id: [{type, value, confidence}]}. A live with no requests (no
    niche syntheses) returns an empty match WITHOUT calling the model.
    """
    settings = settings or get_settings()
    if not requests or not topics:
        return {}  # silent degradation — nothing to match, no LLM cost
    real_ids = {int(t["id"]) for t in topics}
    raw = llm.select(
        system=pp.AUDIENCE_SYSTEM,
        user=pp.build_audience_prompt(list(topics), list(requests)),
        tool=pp.AUDIENCE_TOOL,
        tool_name=pp.AUDIENCE_TOOL["name"],
        model=settings.packaging_audience_model or None,
    )
    out: dict[int, list[dict]] = {}
    for m in raw.get("matches") or []:
        try:
            tid = int(m.get("topic_id"))
        except (TypeError, ValueError):
            continue
        if tid not in real_ids:  # clamp to a real topic — no invented match
            continue
        out.setdefault(tid, []).append({
            "type": m.get("request_type", "other"),
            "value": str(m.get("request_value") or "").strip(),
            "confidence": m.get("confidence", "weak"),
        })
    return {str(tid): reqs for tid, reqs in out.items()}
