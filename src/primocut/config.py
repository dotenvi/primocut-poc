"""Configuração central do PRIMOCUT (pydantic-settings).

Lê de variáveis de ambiente e de um arquivo `.env`. A chave da Anthropic usa o
nome padrão `ANTHROPIC_API_KEY`; as demais opções usam o prefixo `PRIMOCUT_`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PRIMOCUT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- LLM / análise ---
    # Provedor da análise: "anthropic", "openai" ou "openrouter".
    llm_provider: str = "anthropic"

    anthropic_api_key: str | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    anthropic_model: str = "claude-sonnet-5"

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-4o"

    # OpenRouter é compatível com a API da OpenAI (usa o SDK `openai` com base_url).
    openrouter_api_key: str | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Transcrição (faster-whisper) ---
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str = "pt"
    # Sequência de degradação do compute_type na GPU (cabe na VRAM de 8 GB?).
    whisper_compute_fallbacks: str = "float16,int8_float16,int8"

    # --- Resiliência (retry/backoff das etapas) ---
    retry_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    # --- Download (yt-dlp) ---
    # Lista de player_client a tentar quando o extrator do YouTube quebra
    # (vazio = usa o padrão do yt-dlp). Ex.: "android,web".
    ytdlp_player_clients: str = ""

    # --- Windowing da análise ---
    window_target_seconds: float = 20 * 60.0
    window_overlap_seconds: float = 60.0

    # --- Chat ---
    chat_zscore_threshold: float = 2.0

    # --- Armazenamento / retenção ---
    data_dir: Path = Path("data")
    audio_retention_days: int = 7


def get_settings() -> Settings:
    """Instancia as configurações lendo ambiente/.env no momento da chamada."""
    return Settings()
