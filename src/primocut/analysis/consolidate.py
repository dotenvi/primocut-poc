"""Consolidação de spans entre janelas com sobreposição.

Janelas vizinhas compartilham segmentos (a sobreposição), então o mesmo tópico
pode aparecer duas vezes. Fundimos itens que se sobrepõem no tempo, mantendo os
metadados do item de maior score e estendendo o intervalo.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def merge_overlapping(items: list[T]) -> list[T]:
    """Funde itens (com .start/.end/.score) cujos intervalos se sobrepõem.

    Assume que cada item tem os campos `start`, `end`, `score`. Retorna a lista
    consolidada em ordem cronológica.
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda x: (x.start, x.end))
    merged: list[T] = [ordered[0]]
    for it in ordered[1:]:
        prev = merged[-1]
        if it.start < prev.end:  # sobreposição temporal → duplicata
            winner = it if it.score > prev.score else prev
            merged[-1] = winner.model_copy(
                update={"start": min(prev.start, it.start), "end": max(prev.end, it.end)}
            )
        else:
            merged.append(it)
    return merged
