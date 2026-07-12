"""Testes do catálogo de modelos do OpenRouter (fetch injetado, sem rede)."""

from __future__ import annotations

from primocut import openrouter

_SAMPLE = {
    "data": [
        {
            "id": "openai/gpt-4o-mini",
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
            "supported_parameters": ["tools", "temperature"],
        },
        {
            "id": "meta-llama/llama-3.3-70b-instruct:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["tools"],
        },
        {
            "id": "algum/modelo-free-sem-tools:free",
            "pricing": {"prompt": "0", "completion": "0"},
            "supported_parameters": ["temperature"],
        },
    ]
}


def test_fetch_models_uses_injected_fetch():
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return _SAMPLE

    models = openrouter.fetch_models(fetch=fake_fetch)
    assert seen["url"].endswith("/models")
    assert len(models) == 3


def test_filter_tools_only():
    rows = openrouter.filter_models(_SAMPLE["data"], tools_only=True)
    ids = [r["id"] for r in rows]
    assert "openai/gpt-4o-mini" in ids
    assert "meta-llama/llama-3.3-70b-instruct:free" in ids
    assert "algum/modelo-free-sem-tools:free" not in ids  # sem tools é excluído


def test_filter_free_and_tools():
    rows = openrouter.filter_models(_SAMPLE["data"], free_only=True, tools_only=True)
    ids = [r["id"] for r in rows]
    # único grátis E com tools
    assert ids == ["meta-llama/llama-3.3-70b-instruct:free"]
    assert rows[0]["free"] is True and rows[0]["tools"] is True


def test_is_free_and_supports_tools():
    free_model = _SAMPLE["data"][1]
    paid_model = _SAMPLE["data"][0]
    assert openrouter.is_free(free_model) is True
    assert openrouter.is_free(paid_model) is False
    assert openrouter.supports_tools(free_model) is True
