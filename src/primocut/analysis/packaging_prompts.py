"""Prompts and tool schemas for the Phase 3 AI packaging of a cut (opt-in — LLM).

Same forced-tool-use technique as the topic analysis and the Phase 2 enrichment:
the schema IS the contract, so the model answers only in the shape we persist. The
schemas are written in Anthropic shape (`name`/`description`/`input_schema`);
`OpenAILLM.select()` converts them to OpenAI's function format, so packaging is
multi-provider with no branching here (Anthropic default).

The prompt builders turn a `metrics.packaging_signals` context (the LIVE's project
patterns, under three lenses) plus the clip's own content into readable text. Every
measured pattern carries a short ID the model echoes in `patterns_applied` /
`references`, so a proposal can be checked against what was actually measured — a
citation the code cannot match back is dropped, never invented (the never-invent
rule, applied to patterns the way `resolve_span` applies it to timestamps).

The frame-ranking model returns a frame INDEX, never a filename or pixels, resolved
back to a real extracted frame in code (the golden rule, applied to stills).
"""

from __future__ import annotations

from typing import Any

# ------------------------- titles -------------------------

TITLES_SYSTEM = (
    "You are a title strategist for a YouTube clip channel in a specific niche. "
    "You propose titles for ONE clip, in PT-BR, grounded ONLY in the measured "
    "niche patterns you are given. Every title must cite which measured patterns "
    "it applies, by the pattern id shown — never cite a pattern that is not in the "
    "list, and never claim a differential the niche does not have. When the niche "
    "is flat (no differentials), do not invent one: lean on the subject and on the "
    "value that carries (a name, a quoted line), and say so plainly. Keep titles "
    "tight and specific to THIS clip's content."
)


def titles_tool(k: int) -> dict[str, Any]:
    """The forced tool proposing exactly `k` titles, each citing its patterns."""
    return {
        "name": "propose_titles",
        "description": (
            f"Propose exactly {k} candidate titles for the clip, each citing which "
            "measured niche patterns it applies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "titles": {
                    "type": "array",
                    "minItems": k,
                    "maxItems": k,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "The proposed title, in PT-BR.",
                            },
                            "length_chars": {
                                "type": "integer",
                                "description": "The title's character count.",
                            },
                            "patterns_applied": {
                                "type": "array",
                                "description": (
                                    "The measured patterns this title applies, by "
                                    "the id shown in the prompt. Empty when the "
                                    "niche is flat and the title leans on content."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "kind": {
                                            "type": "string",
                                            "enum": ["differential", "convention",
                                                     "carries_value"],
                                        },
                                        "reference": {
                                            "type": "string",
                                            "description": (
                                                "The pattern id exactly as shown "
                                                "(e.g. 'D1', 'C2', 'V1')."
                                            ),
                                        },
                                    },
                                    "required": ["kind", "reference"],
                                },
                            },
                            "rationale": {
                                "type": "string",
                                "description": "One sentence: why it fits this niche.",
                            },
                        },
                        "required": ["title", "length_chars", "patterns_applied",
                                     "rationale"],
                    },
                }
            },
            "required": ["titles"],
        },
    }


# ------------------------- thumbnail brief (NO image generation) -------------------------

BRIEF_SYSTEM = (
    "You write a thumbnail BRIEF for one YouTube clip — a specification for a human "
    "designer, NOT an image and NOT a prompt for an image generator. Ground every "
    "recommendation in the measured niche patterns you are given, and reference the "
    "real outlier examples by their video id. When the niche is flat, say honestly "
    "that packaging is not the lever here and keep the brief minimal, emphasising "
    "the subject and the value that carries. Never invent a pattern the niche lacks."
)

BRIEF_TOOL = {
    "name": "record_thumbnail_brief",
    "description": "Record a structured thumbnail brief for a clip (no image).",
    "input_schema": {
        "type": "object",
        "properties": {
            "composition": {
                "type": "object",
                "properties": {
                    "face_count": {"type": "integer",
                                   "description": "Recommended number of faces."},
                    "expression": {
                        "type": "string",
                        "enum": ["neutral", "happy", "shock", "anger", "sadness",
                                 "fear", "disgust"],
                    },
                    "framing": {
                        "type": "string",
                        "enum": ["single", "confrontation", "reaction", "object"],
                    },
                },
                "required": ["face_count", "expression", "framing"],
            },
            "text": {
                "type": "object",
                "properties": {
                    "word_count": {"type": "integer",
                                   "description": "Recommended overlaid-text word count."},
                    "suggestion": {"type": "string",
                                   "description": "Suggested overlaid text, in PT-BR."},
                },
                "required": ["word_count", "suggestion"],
            },
            "colors": {
                "type": "object",
                "properties": {
                    "palette": {
                        "type": "array", "maxItems": 3,
                        "items": {
                            "type": "string",
                            "enum": ["red", "orange", "yellow", "green", "blue",
                                     "purple", "pink", "brown", "black", "white",
                                     "gray"],
                        },
                    },
                    "contrast": {"type": "string",
                                 "enum": ["low", "medium", "high"]},
                },
                "required": ["palette", "contrast"],
            },
            "graphics": {
                "type": "object",
                "properties": {
                    "arrow": {"type": "boolean"},
                    "circle_highlight": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["arrow", "circle_highlight", "note"],
            },
            "references": {
                "type": "array",
                "description": "Real outlier examples that used the cited patterns.",
                "items": {
                    "type": "object",
                    "properties": {
                        "video_id": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["video_id", "why"],
                },
            },
            "pattern_notes": {
                "type": "string",
                "description": "Which measured patterns shaped this brief, by id.",
            },
        },
        "required": ["composition", "text", "colors", "graphics", "references",
                     "pattern_notes"],
    },
}


# ------------------------- frame ranking (opt-in, vision) -------------------------

FRAME_RANK_SYSTEM = (
    "You rank candidate thumbnail frames extracted from a clip against a thumbnail "
    "brief. Judge expression strength, sharpness and framing. Refer to each frame "
    "ONLY by its index [i] — never describe it as a file. Return a score for every "
    "frame you are shown and name the single best index."
)

FRAME_RANK_TOOL = {
    "name": "rank_frames",
    "description": "Rank the candidate frames against the thumbnail brief.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rankings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "frame": {"type": "integer",
                                  "description": "The frame index [i] shown."},
                        "score": {"type": "number",
                                  "description": "Overall fit, 0..1."},
                        "expression": {"type": "number", "description": "0..1."},
                        "sharpness": {"type": "number", "description": "0..1."},
                        "framing": {"type": "number", "description": "0..1."},
                        "fits_brief": {"type": "boolean"},
                        "note": {"type": "string"},
                    },
                    "required": ["frame", "score", "expression", "sharpness",
                                 "framing", "fits_brief", "note"],
                },
            },
            "best_frame": {"type": "integer",
                           "description": "The index of the single best frame."},
        },
        "required": ["rankings", "best_frame"],
    },
}


# ------------------------- audience match (cheap, cached per live) -------------------------

AUDIENCE_SYSTEM = (
    "You match a niche's audience requests to a live's topics. A request matches a "
    "topic only when the topic genuinely delivers what the audience explicitly "
    "asked for — never a loose thematic overlap. Refer to a topic ONLY by its "
    "topic_id, and echo a request's value exactly as given. Return an empty list "
    "when nothing matches."
)

AUDIENCE_TOOL = {
    "name": "match_audience",
    "description": "Match the niche's audience requests to the live's topics.",
    "input_schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic_id": {"type": "integer",
                                     "description": "The topic id shown in the list."},
                        "request_type": {
                            "type": "string",
                            "enum": ["guest", "topic", "format", "other"],
                        },
                        "request_value": {"type": "string",
                                          "description": "The request value, echoed."},
                        "confidence": {"type": "string",
                                       "enum": ["strong", "weak"]},
                    },
                    "required": ["topic_id", "request_type", "request_value",
                                 "confidence"],
                },
            }
        },
        "required": ["matches"],
    },
}


# ------------------------- prompt builders -------------------------


def pattern_index(context: dict) -> dict[str, dict]:
    """Assign a short, stable id to every measured pattern in the context.

    `D#` differentials, `C#` conventions, `V#` carries-value. The same map is used
    to render the prompt AND to validate the model's citations, so a `reference`
    the model returns is resolvable exactly when it names a real measured pattern.
    """
    out: dict[str, dict] = {}
    for i, d in enumerate(context.get("differential", []), 1):
        out[f"D{i}"] = {"kind": "differential", **d}
    for i, c in enumerate(context.get("conventions", []), 1):
        out[f"C{i}"] = {"kind": "convention", **c}
    i = 1
    for field, rows in (context.get("carries_value") or {}).items():
        for r in rows:
            out[f"V{i}"] = {"kind": "carries_value", "field": field, **r}
            i += 1
    return out


def _render_patterns(context: dict, ids: dict[str, dict]) -> str:
    lines: list[str] = []
    diffs = [(k, v) for k, v in ids.items() if v["kind"] == "differential"]
    convs = [(k, v) for k, v in ids.items() if v["kind"] == "convention"]
    vals = [(k, v) for k, v in ids.items() if v["kind"] == "carries_value"]

    lines.append("DIFFERENTIALS (what separates outliers from the rest):")
    if diffs:
        for k, v in diffs:
            lift = f"{v['lift']:.1f}x" if v.get("lift") else "n/a"
            lines.append(
                f"  [{k}] {v['attribute']} ({v['source']}) — {v['verdict']}, "
                f"lift {lift}: {v['freq_outlier']:.0%} of outliers vs "
                f"{v['freq_control']:.0%} of the rest (n={v['n_outlier']})"
            )
    else:
        lines.append("  (none — this niche is flat on packaging; do not invent one)")

    lines.append("CONVENTIONS (what the niche requires — table-stakes, not a lever):")
    if convs:
        for k, v in convs:
            lines.append(f"  [{k}] {v['attribute']} ({v['source']}) — "
                         f"{v['freq']:.0%} of the niche (n={v['n']})")
    else:
        lines.append("  (none measured)")

    lines.append("CARRIES VALUE (specific values that carry views in this niche):")
    if vals:
        for k, v in vals:
            lift = f"{v['lift']:.1f}x" if v.get("lift") else "unique to outliers"
            lines.append(
                f"  [{k}] {v['field']}: \"{v['value']}\" — {v['videos_outlier']} "
                f"outlier(s) vs {v['videos_control']} other(s), {lift}"
            )
    else:
        lines.append("  (none measured)")
    return "\n".join(lines)


def _render_examples(context: dict) -> str:
    examples = context.get("examples", [])
    if not examples:
        return "REAL OUTLIER EXAMPLES: (none enriched yet)"
    lines = ["REAL OUTLIER EXAMPLES (real titles/attributes — cite by video id):"]
    for ex in examples:
        mm = ex.get("median_multiple")
        mult = f"{mm:.1f}x median" if mm else ""
        lines.append(f"  [{ex['video_id']}] {mult} — {ex['title']!r}")
        ta = ex.get("title_attrs") or {}
        names = ", ".join(ta.get("person_names") or [])
        if names:
            lines.append(f"      names: {names}")
        quoted = (ta.get("quoted_speech") or "").strip()
        if quoted:
            lines.append(f"      quote: {quoted!r}")
    return "\n".join(lines)


def _render_clip(clip: dict) -> str:
    lines = [f"CLIP TO PACKAGE: {clip.get('title', '')!r}"]
    if clip.get("summary"):
        lines.append(f"  summary: {clip['summary']}")
    excerpt = (clip.get("transcript_excerpt") or "").strip()
    if excerpt:
        lines.append(f"  transcript excerpt:\n{excerpt}")
    return "\n".join(lines)


def _render_niche(context: dict) -> str:
    n = context["niche"]
    flat = " — FLAT niche: packaging is not the lever here" if context.get("flat") else ""
    return (f"NICHE: {n['project_name']} ({n['channels']} competitor channel(s), "
            f"{n['outliers']} {n['format']} outlier(s)){flat}")


def build_titles_prompt(clip: dict, context: dict, k: int, ids: dict[str, dict]) -> str:
    """The user turn for the title pass. `ids` = `pattern_index(context)`."""
    return "\n\n".join([
        _render_niche(context),
        _render_clip(clip),
        _render_patterns(context, ids),
        _render_examples(context),
        f"Propose exactly {k} titles for the clip. Each must cite which measured "
        f"patterns it applies, by the id shown (e.g. D1, C2, V1).",
    ])


def build_brief_prompt(clip: dict, context: dict, ids: dict[str, dict]) -> str:
    """The user turn for the thumbnail-brief pass."""
    return "\n\n".join([
        _render_niche(context),
        _render_clip(clip),
        _render_patterns(context, ids),
        _render_examples(context),
        "Write one thumbnail brief for the clip. Ground it in the measured "
        "patterns and reference the real outlier examples by video id.",
    ])


def build_audience_prompt(topics: list[dict], requests: list[dict]) -> str:
    """The user turn for the audience-match pass: the live's topics + niche demand."""
    tlines = ["LIVE TOPICS (match by topic_id):"]
    for t in topics:
        summary = f" — {t['summary']}" if t.get("summary") else ""
        tlines.append(f"  [{t['id']}] {t['title']}{summary}")
    rlines = ["NICHE AUDIENCE REQUESTS:"]
    for r in requests:
        rlines.append(f"  ({r['type']}) {r['value']} — asked by {r['videos']} video(s)")
    return "\n\n".join([
        "\n".join(tlines),
        "\n".join(rlines),
        "Return every topic that genuinely delivers an explicitly requested thing.",
    ])
