"""Shared test configuration.

Isolates settings from the real environment: tests must NOT depend on the
developer's `.env` nor on environment variables (e.g. when `.env` is configured
for OpenRouter, `Settings()` must not inherit that and break tests that assume
the default provider/keys).
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
    """Make `Settings()` ignore `.env` and the PRIMOCUT env vars during tests."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
