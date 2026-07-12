"""Passagem MACRO: extração de tópicos (10–20 por live) via LLM + consolidação."""

from __future__ import annotations

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import Topic, Transcript
from .consolidate import merge_overlapping
from .llm import SelectionLLM
from .parse import coerce_items, span_indices
from .prompts import TOPICS_SYSTEM, TOPICS_TOOL
from .runner import select_windows
from .windowing import make_windows

logger = get_logger(__name__)

MAX_TOPICS = 20


def extract_topics(
    transcript: Transcript,
    llm: SelectionLLM,
    settings: Settings | None = None,
    *,
    warnings: list[str] | None = None,
) -> list[Topic]:
    settings = settings or get_settings()
    windows = make_windows(
        transcript,
        target_seconds=settings.window_target_seconds,
        overlap_seconds=settings.window_overlap_seconds,
    )

    raw: list[Topic] = []
    for payload in select_windows(
        windows, llm, system=TOPICS_SYSTEM, tool=TOPICS_TOOL, tool_name="record_topics",
        settings=settings, label="Tópicos", warnings=warnings,
    ):
        for item in coerce_items(payload, "topics"):
            try:
                start, end = transcript.resolve_span(*span_indices(item))
                raw.append(
                    Topic(
                        title=str(item["title"]),
                        summary=str(item.get("summary", "")),
                        start=start,
                        end=end,
                        score=_clamp_score(item.get("score", 0.0)),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Ignorando tópico malformado %r (%s)", item, exc)

    topics = merge_overlapping(raw)
    if len(topics) > MAX_TOPICS:
        topics = sorted(topics, key=lambda t: t.score, reverse=True)[:MAX_TOPICS]
        topics = sorted(topics, key=lambda t: t.start)
    return topics


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
