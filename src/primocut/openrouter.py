"""Consulta o catálogo de modelos do OpenRouter (para achar modelos com tool use).

A listagem de modelos do OpenRouter é pública (não precisa de chave nem saldo).
Útil para descobrir modelos GRÁTIS que suportam ferramentas (function calling),
já que a nossa análise depende disso.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "primocut"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (URL fixa/interna)
        return json.loads(resp.read().decode("utf-8"))


def fetch_models(
    base_url: str = DEFAULT_BASE_URL,
    *,
    fetch: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    """Retorna a lista crua de modelos do OpenRouter (`data`)."""
    fetch = fetch or _http_get_json
    payload = fetch(base_url.rstrip("/") + "/models")
    return list(payload.get("data", []))


def is_free(model: dict[str, Any]) -> bool:
    """Modelo é grátis quando prompt e completion custam 0."""
    pricing = model.get("pricing") or {}
    try:
        return float(pricing.get("prompt", "0")) == 0.0 and float(
            pricing.get("completion", "0")
        ) == 0.0
    except (TypeError, ValueError):
        return False


def supports_tools(model: dict[str, Any]) -> bool:
    return "tools" in (model.get("supported_parameters") or [])


def filter_models(
    models: list[dict[str, Any]],
    *,
    free_only: bool = False,
    tools_only: bool = True,
) -> list[dict[str, Any]]:
    """Filtra e normaliza os modelos para `{id, free, tools}`, ordenados por id."""
    out: list[dict[str, Any]] = []
    for m in models:
        tools = supports_tools(m)
        free = is_free(m)
        if tools_only and not tools:
            continue
        if free_only and not free:
            continue
        out.append({"id": m.get("id", "?"), "free": free, "tools": tools})
    return sorted(out, key=lambda x: x["id"])
