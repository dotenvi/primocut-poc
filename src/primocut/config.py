"""Central PRIMOCUT configuration (pydantic-settings).

Reads from environment variables and a `.env` file. The Anthropic key uses the
standard name `ANTHROPIC_API_KEY`; the other options use the `PRIMOCUT_` prefix.
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

    # --- LLM / analysis ---
    # Analysis provider: "anthropic", "openai" or "openrouter".
    llm_provider: str = "anthropic"

    anthropic_api_key: str | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    anthropic_model: str = "claude-sonnet-5"

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_model: str = "gpt-4o"

    # OpenRouter is OpenAI-API compatible (uses the `openai` SDK with a base_url).
    openrouter_api_key: str | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Transcription (faster-whisper) ---
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str = "pt"
    # GPU compute_type degradation sequence (does it fit in 8 GB of VRAM?).
    whisper_compute_fallbacks: str = "float16,int8_float16,int8"

    # --- Resilience (stage retry/backoff) ---
    retry_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    # --- Download (yt-dlp) ---
    # List of player_client values to try when the YouTube extractor breaks
    # (empty = use yt-dlp's default). E.g. "android,web".
    ytdlp_player_clients: str = ""

    # --- Analysis windowing ---
    window_target_seconds: float = 20 * 60.0
    window_overlap_seconds: float = 60.0

    # --- Chat ---
    chat_zscore_threshold: float = 2.0

    # --- Storage ---
    # Base dir for the SQLite DB (see `db_path`). Per-video artifacts are not
    # stored here: the CLI exports them to `--output` and the audio (both CLI
    # and worker) uses a throwaway temp dir.
    data_dir: Path = Path("data")

    # --- Phase 1: database, queue, server ---
    db_path: Path = Path("data/primocut.db")
    redis_url: str = "redis://localhost:6379/0"
    rq_queue: str = "primocut"
    # RQ job timeout (s). The pipeline can take tens of minutes (first-time model
    # download + transcription of long lives). Generous default: 6h.
    rq_job_timeout: int = 6 * 3600
    # GPU lock: guarantees 1 transcription at a time, regardless of how many
    # workers run. `timeout` = lease (auto-releases if the worker dies); it must
    # be longer than the longest possible transcription.
    gpu_lock_timeout: int = 2 * 3600
    serve_host: str = "127.0.0.1"
    serve_port: int = 8000

    # --- Phase 2: competitor modeling (YouTube Data API v3) ---
    # Public-data API key (free tier: 10,000 quota units/day). The env_prefix
    # makes this read from PRIMOCUT_YOUTUBE_API_KEY.
    youtube_api_key: str | None = None
    # A video is treated as a Short when its duration is at most this many
    # seconds. The Data API does not flag Shorts, so this is a heuristic
    # (refined in a later phase); it feeds the separate Shorts baseline.
    youtube_short_max_seconds: int = 60
    # How many recent uploads to backfill when a channel is new to the system.
    youtube_backfill_limit: int = 120


def get_settings() -> Settings:
    """Instantiate the settings reading environment/.env at call time."""
    return Settings()
