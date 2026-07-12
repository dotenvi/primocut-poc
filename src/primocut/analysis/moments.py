"""Passagem FINA: detecção de momentos candidatos a corte via LLM + consolidação.

Mesma mecânica dos tópicos (seleção de índices → resolução em segundos), mas sem
forçar o teto de 10–20: momentos são pontuais e podem ser muitos.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import CutMoment, Transcript
from .consolidate import merge_overlapping
from .llm import SelectionLLM
from .parse import coerce_items, span_indices
from .prompts import MOMENTS_SYSTEM, MOMENTS_TOOL
from .runner import select_windows
from .windowing import make_windows

logger = get_logger(__name__)


def extract_moments(
    transcript: Transcript,
    llm: SelectionLLM,
    settings: Settings | None = None,
    *,
    warnings: list[str] | None = None,
) -> list[CutMoment]:
    settings = settings or get_settings()
    windows = make_windows(
        transcript,
        target_seconds=settings.window_target_seconds,
        overlap_seconds=settings.window_overlap_seconds,
    )

    raw: list[CutMoment] = []
    for payload in select_windows(
        windows, llm, system=MOMENTS_SYSTEM, tool=MOMENTS_TOOL, tool_name="record_moments",
        settings=settings, label="Momentos", warnings=warnings,
    ):
        for item in coerce_items(payload, "moments"):
            try:
                start, end = transcript.resolve_span(*span_indices(item))
                raw.append(
                    CutMoment(
                        title=str(item["title"]),
                        reason=str(item.get("reason", "")),
                        start=start,
                        end=end,
                        score=max(0.0, min(1.0, float(item.get("score", 0.0)))),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("Ignorando momento malformado %r (%s)", item, exc)

    return merge_overlapping(raw)
