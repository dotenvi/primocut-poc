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
    # A video is "recent" (part of the 6h snapshot pass) for this many days.
    youtube_recent_days: int = 7
    # Top-of-uploads window scanned each cycle to detect newly published videos.
    youtube_uploads_scan_limit: int = 15

    # --- Phase 2: snapshot scheduler (rq-scheduler) ---
    # Daily pass over ALL videos of active channels; 6-hourly pass over videos
    # younger than `youtube_recent_days`. The daily hour is offset from the 6h
    # grid so the two never fire in the same minute.
    snapshot_daily_cron: str = "0 3 * * *"
    snapshot_recent_cron: str = "0 */6 * * *"

    # --- Phase 2: metrics engine (deterministic, computed on read) ---
    # Outlier score = latest views / channel baseline. The baseline is the MEDIAN
    # (never the mean) of the same-format videos in a rolling window = the smaller
    # of "the last N videos" and "the last D days"; separate baselines for Shorts
    # and long-form. A video needs at least `outlier_min_sample` same-format peers
    # to be scored, else its status is "insufficient_data".
    outlier_window_videos: int = 50
    outlier_window_days: int = 180
    outlier_min_sample: int = 10
    # A video younger than this is scored "provisional" (it has not accumulated
    # views against a mature baseline yet).
    outlier_provisional_days: int = 7
    # Bracket thresholds (comma-separated, ascending) labelling the score tier.
    outlier_brackets: str = "3,6,10"
    # Views-per-hour is derived exclusively from snapshot deltas. The "current"
    # VPH is the delta over this trailing window (hours).
    vph_current_window_hours: int = 24
    # Resurgence: an old video (age >= min_age_days) whose current VPH is at least
    # `resurgence_k` times its recent trailing-average VPH (mean of the pairwise
    # deltas over the last `resurgence_baseline_days`). `min_vph` guards against a
    # ~0 baseline producing a spurious ratio.
    resurgence_min_age_days: int = 30
    resurgence_k: float = 3.0
    resurgence_baseline_days: int = 7
    resurgence_min_vph: float = 1.0
    # Packaging impact: mean VPH this many days before vs after a change (the
    # "after" side is computed only once the window has fully elapsed).
    packaging_impact_window_days: int = 7
    # Conditional-distribution guard: a title/thumbnail pattern is reported for a
    # project only if at least this many outlier videos exhibit it.
    pattern_min_occurrence: int = 15
    # The bracket (multiplier) at/above which a video counts as an outlier for the
    # per-project pattern distributions. Also the batch-enrichment target.
    pattern_outlier_bracket: float = 3.0

    # --- Phase 2: AI packaging enrichment (opt-in — costs LLM) ---
    # Model overrides for the enrichment calls. EMPTY means "use the selected
    # provider's configured model" (see llm_provider above), which keeps the
    # default multi-provider behavior intact. Set one to spend less — e.g.
    # PRIMOCUT_ENRICH_TITLE_MODEL=claude-haiku-4-5 for the title pass. A model id
    # is provider-specific, so an override is only valid for the provider in use;
    # the vision model must support image input.
    enrich_title_model: str = ""
    enrich_vision_model: str = ""


def get_settings() -> Settings:
    """Instantiate the settings reading environment/.env at call time."""
    return Settings()
