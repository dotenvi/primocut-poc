"""LLM client for structured segment selection (Anthropic).

The rest of the analysis depends only on the `SelectionLLM` protocol, so tests
inject a fake and run without network. The real implementation forces a tool
call to guarantee the schema (segment indices).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..config import Settings, get_settings
from ..logging_conf import get_logger

logger = get_logger(__name__)


class ToolCallError(RuntimeError):
    """The model did not return the forced tool/function call.

    This usually means the chosen model does not support tool use (function
    calling) — essential for our analysis, which forces the LLM to select
    segment indices instead of generating free text.
    """


class SelectionLLM(Protocol):
    def select(
        self, *, system: str, user: str, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        """Return the input (dict) of the forced tool call."""
        ...


class AnthropicLLM:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy import

            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    def select(
        self, *, system: str, user: str, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        client = self._get_client()
        response = client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=4096,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            thinking={"type": "disabled"},  # deterministic extraction; no thinking
            messages=[{"role": "user", "content": user}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise ToolCallError(
            f"Model '{self.settings.anthropic_model}' (anthropic) did not return the "
            f"tool call '{tool_name}'. Check whether the model supports tool use."
        )


class OpenAILLM:
    """`SelectionLLM` via OpenAI function calling (same interface).

    Serves as the base for any OpenAI-compatible API (e.g. OpenRouter):
    subclasses override key/model/base_url.
    """

    provider_label = "OpenAI"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # Hooks overridable by subclasses (e.g. OpenRouter) --------------------------
    def _api_key(self) -> str | None:
        return self.settings.openai_api_key

    def _model(self) -> str:
        return self.settings.openai_model

    def _base_url(self) -> str | None:
        return None  # None → default OpenAI endpoint

    def _default_headers(self) -> dict[str, str] | None:
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai  # lazy import
            except ImportError as exc:
                raise RuntimeError(
                    f"Provider '{self.provider_label}' uses the OpenAI SDK, which is not "
                    "installed. Install it with: "
                    "/usr/bin/python3 -m pip --python .venv/bin/python install openai"
                ) from exc

            self._client = openai.OpenAI(
                api_key=self._api_key(),
                base_url=self._base_url(),
                default_headers=self._default_headers(),
            )
        return self._client

    def select(
        self, *, system: str, user: str, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        client = self._get_client()
        # Convert our schema {name, description, input_schema} to OpenAI's
        # function format {name, description, parameters}.
        openai_tool = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        }
        response = client.chat.completions.create(
            model=self._model(),
            tools=[openai_tool],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        tool_calls = response.choices[0].message.tool_calls or []
        for call in tool_calls:
            if call.function.name == tool_name:
                return json.loads(call.function.arguments or "{}")
        raise ToolCallError(
            f"Model '{self._model()}' ({self.provider_label}) did not return the function "
            f"call '{tool_name}' — it likely does not support tool use (function calling). "
            "Choose a model that supports tools."
        )


class OpenRouterLLM(OpenAILLM):
    """OpenRouter (OpenAI-compatible API): only key, model and base_url differ."""

    provider_label = "OpenRouter"

    def _api_key(self) -> str | None:
        return self.settings.openrouter_api_key

    def _model(self) -> str:
        return self.settings.openrouter_model

    def _base_url(self) -> str | None:
        return self.settings.openrouter_base_url

    def _default_headers(self) -> dict[str, str] | None:
        # Optional headers recommended by OpenRouter (ranking/attribution).
        return {"X-Title": "primocut"}


def get_llm(settings: Settings | None = None) -> SelectionLLM:
    """Factory for the analysis LLM based on `settings.llm_provider`."""
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicLLM(settings)
    if provider == "openai":
        return OpenAILLM(settings)
    if provider == "openrouter":
        return OpenRouterLLM(settings)
    raise ValueError(
        f"unknown llm_provider: {settings.llm_provider!r} "
        "(use 'anthropic', 'openai' or 'openrouter')"
    )
