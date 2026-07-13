"""Prompts and tool schemas for the LLM analysis.

Golden rule (technical guideline #2): the LLM NEVER invents timestamps. It
receives indexed segments (`[i] M:SS text`) and returns pairs of segment
INDICES. The code resolves the indices into seconds. That is why the tools only
accept integer `start_index`/`end_index` — there is no time field.
"""

from __future__ import annotations

_ANTI_INVENTION = (
    "You receive the transcript as numbered lines in the format `[i] M:SS text`, "
    "where `i` is the segment index. To mark a passage, ALWAYS return the "
    "start and end segment indices (start_index/end_index) — NEVER invent times "
    "nor return minute/second numbers. Use only indices that exist in the "
    "transcript."
)

TOPICS_SYSTEM = (
    "You are a podcast/live clip editor working in PT-BR. Your task is to segment "
    "the transcript into MACRO TOPICS (subjects discussed), in chronological "
    "order. Create one topic per genuinely distinct subject — do NOT aim for any "
    "particular number of topics: a short segment may have a few, a long one many. "
    "Do not merge unrelated subjects to hit a target, nor split a single subject "
    "into fragments. Each topic has a short title, a 1-sentence summary and a "
    "score from 0 to 1 of its potential to become a clip.\n" + _ANTI_INVENTION
)

TOPICS_TOOL = {
    "name": "record_topics",
    "description": "Records the macro topics identified in the window.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "start_index": {"type": "integer"},
                        "end_index": {"type": "integer"},
                        "score": {"type": "number"},
                    },
                    "required": ["title", "summary", "start_index", "end_index", "score"],
                },
            }
        },
        "required": ["topics"],
    },
}
