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

    # --- Fase 1: banco, fila, servidor ---
    db_path: Path = Path("data/primocut.db")
    redis_url: str = "redis://localhost:6379/0"
    rq_queue: str = "primocut"
    # Timeout do job RQ (s). O pipeline pode levar dezenas de min (download do
    # modelo na 1ª vez + transcrição de lives longas). Default folgado: 6h.
    rq_job_timeout: int = 6 * 3600
    # Lock de GPU: garante 1 transcrição por vez, independentemente de quantos
    # workers rodem. `timeout` = lease (auto-libera se o worker morrer); deve ser
    # maior que a transcrição mais longa possível.
    gpu_lock_timeout: int = 2 * 3600
    serve_host: str = "127.0.0.1"
    serve_port: int = 8000


def get_settings() -> Settings:
    """Instancia as configurações lendo ambiente/.env no momento da chamada."""
    return Settings()
