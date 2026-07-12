"""Configuração compartilhada dos testes.

Isola as configurações do ambiente real: os testes NÃO devem depender do `.env`
do desenvolvedor nem de variáveis de ambiente (ex.: quando o `.env` está
configurado para OpenRouter, `Settings()` não pode herdar isso e quebrar testes
que assumem o provedor/chaves padrão).
"""

from __future__ import annotations

import pytest

from primocut.config import Settings

_ENV_VARS = (
    "PRIMOCUT_LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "PRIMOCUT_ANTHROPIC_MODEL",
    "PRIMOCUT_OPENAI_MODEL",
    "PRIMOCUT_OPENROUTER_MODEL",
    "PRIMOCUT_OPENROUTER_BASE_URL",
    "PRIMOCUT_WHISPER_MODEL",
    "PRIMOCUT_WHISPER_DEVICE",
    "PRIMOCUT_WHISPER_COMPUTE_TYPE",
)


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    """Faz `Settings()` ignorar o `.env` e as env vars do PRIMOCUT durante os testes."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
