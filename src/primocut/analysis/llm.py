"""Cliente de LLM para seleção estruturada de segmentos (Anthropic).

O resto da análise depende apenas do protocolo `SelectionLLM`, então os testes
injetam um fake e rodam sem rede. A implementação real força uma tool call para
garantir o schema (índices de segmento).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..config import Settings, get_settings
from ..logging_conf import get_logger

logger = get_logger(__name__)


class ToolCallError(RuntimeError):
    """O modelo não retornou a tool/function call forçada.

    Normalmente significa que o modelo escolhido não suporta tool use (function
    calling) — essencial para a nossa análise, que força o LLM a selecionar
    índices de segmento em vez de gerar texto livre.
    """


class SelectionLLM(Protocol):
    def select(
        self, *, system: str, user: str, tool: dict[str, Any], tool_name: str
    ) -> dict[str, Any]:
        """Retorna o input (dict) da tool call forçada."""
        ...


class AnthropicLLM:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # import tardio

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
            thinking={"type": "disabled"},  # extração determinística; sem thinking
            messages=[{"role": "user", "content": user}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise ToolCallError(
            f"O modelo '{self.settings.anthropic_model}' (anthropic) não retornou a "
            f"tool call '{tool_name}'. Verifique se o modelo suporta tool use."
        )


class OpenAILLM:
    """`SelectionLLM` via function calling da OpenAI (mesma interface).

    Serve de base para qualquer API compatível com a OpenAI (ex.: OpenRouter):
    subclasses sobrescrevem chave/modelo/base_url.
    """

    provider_label = "OpenAI"

    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    # Hooks sobrescrevíveis por subclasses (ex.: OpenRouter) ---------------------
    def _api_key(self) -> str | None:
        return self.settings.openai_api_key

    def _model(self) -> str:
        return self.settings.openai_model

    def _base_url(self) -> str | None:
        return None  # None → endpoint padrão da OpenAI

    def _default_headers(self) -> dict[str, str] | None:
        return None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai  # import tardio
            except ImportError as exc:
                raise RuntimeError(
                    f"O provedor '{self.provider_label}' usa o SDK da OpenAI, que não está "
                    "instalado. Instale com: "
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
        # Converte nosso schema {name, description, input_schema} para o formato
        # de função da OpenAI {name, description, parameters}.
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
            f"O modelo '{self._model()}' ({self.provider_label}) não retornou a function "
            f"call '{tool_name}' — provavelmente não suporta tool use (function calling). "
            "Escolha um modelo com suporte a ferramentas."
        )


class OpenRouterLLM(OpenAILLM):
    """OpenRouter (API compatível com a OpenAI): só muda chave, modelo e base_url."""

    provider_label = "OpenRouter"

    def _api_key(self) -> str | None:
        return self.settings.openrouter_api_key

    def _model(self) -> str:
        return self.settings.openrouter_model

    def _base_url(self) -> str | None:
        return self.settings.openrouter_base_url

    def _default_headers(self) -> dict[str, str] | None:
        # Cabeçalhos opcionais recomendados pelo OpenRouter (ranking/atribuição).
        return {"X-Title": "primocut"}


def get_llm(settings: Settings | None = None) -> SelectionLLM:
    """Factory do LLM de análise conforme `settings.llm_provider`."""
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicLLM(settings)
    if provider == "openai":
        return OpenAILLM(settings)
    if provider == "openrouter":
        return OpenRouterLLM(settings)
    raise ValueError(
        f"llm_provider desconhecido: {settings.llm_provider!r} "
        "(use 'anthropic', 'openai' ou 'openrouter')"
    )
