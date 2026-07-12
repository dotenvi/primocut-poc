"""Leitura tolerante das respostas do LLM.

O LLM às vezes devolve o array fora do formato ideal (um dict indexado, itens
que não são objetos, campos faltando). Em vez de estourar exceção e derrubar a
análise inteira — perdendo o trabalho já pago —, normalizamos e ignoramos apenas
os itens ruins, com aviso no log.
"""

from __future__ import annotations

from typing import Any

from ..logging_conf import get_logger

logger = get_logger(__name__)


def coerce_items(payload: Any, key: str) -> list[dict[str, Any]]:
    """Extrai `payload[key]` como uma lista de dicts, tolerando variações."""
    items = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(items, dict):
        # alguns modelos devolvem {"0": {...}, "1": {...}} em vez de lista
        items = list(items.values())
    if items is None:
        return []
    if not isinstance(items, list):
        logger.warning("Análise: campo %r não é uma lista (%s); ignorando.", key, type(items).__name__)
        return []
    good = [it for it in items if isinstance(it, dict)]
    if len(good) != len(items):
        logger.warning("Análise: %d item(ns) em %r não eram objetos e foram ignorados.",
                       len(items) - len(good), key)
    return good


def span_indices(item: dict[str, Any]) -> tuple[int, int]:
    """Lê start_index/end_index como inteiros. Levanta KeyError/ValueError/TypeError."""
    return int(item["start_index"]), int(item["end_index"])
