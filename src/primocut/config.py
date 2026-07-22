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
    # 180 is YouTube's own Shorts ceiling since October 2024. A long-form video
    # under 3 minutes is the false positive this accepts; the reverse (missing
    # every 61-180s Short) was the common case in practice.
    youtube_short_max_seconds: int = 180
    # How many recent uploads to backfill when a channel is new to the system.
    youtube_backfill_limit: int = 120
    # A video is "recent" (part of the 6h snapshot pass) for this many days.
    youtube_recent_days: int = 7
    # Top-of-uploads window scanned each cycle to detect newly published videos.
    youtube_uploads_scan_limit: int = 15
    # Pages of top comments collected when a video becomes a definitive outlier
    # (commentThreads.list: 1 quota unit per page of 100). Collection is automatic
    # and free of LLM cost; the tail of a long thread adds noise, not signal.
    # Must be >= 1: collection is once-per-video, so a zero here would settle every
    # outlier it touched as "collected, no comments" and never revisit them.
    youtube_comment_pages: int = Field(default=2, ge=1)

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
    # to be scored, else its status is "insufficient_sample".
    outlier_window_videos: int = 50
    outlier_window_days: int = 180
    outlier_min_sample: int = 10
    # A video younger than this is scored "provisional" (it has not accumulated
    # views against a mature baseline yet).
    outlier_provisional_days: int = 7
    # How a video is classified into a band (relevant/strong/exceptional).
    # "percentile" (default): the band cutoffs are percentiles of the channel's
    # own same-format views (p90/p97/p99), so ~10% of a channel land in the tail.
    # "fixed_multiple" (legacy): cutoffs are multiples of the median (3x/6x/10x) —
    # demoted because on the skewed YouTube view distribution 3× the median lands
    # around the channel's p72, not the tail, marking ~30% of videos as outliers.
    # Each basis reads its own three ascending cutoffs.
    band_basis: str = "percentile"  # "percentile" | "fixed_multiple"
    band_cutoffs_percentile: str = "90,97,99"
    band_cutoffs_fixed: str = "3,6,10"
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
    # Conditional-distribution guard: a title/thumbnail attribute is reported only
    # when the 95% Wilson score intervals of its outlier and control frequencies do
    # NOT overlap (z from `pattern_confidence`, 1.96 = 95%). Non-overlap of two 95%
    # intervals is conservative (~alpha 0.005) BY DESIGN — it absorbs the multiple-
    # comparisons correction across the ~15 attributes tested per report. There is
    # deliberately no minimum-n floor: a small group yields a wide interval that
    # never separates, so the interval self-regulates. The outlier group is every
    # video with a band; the control is the banded==null, mature, sufficient-
    # baseline videos (provisional and insufficient-sample videos are censored out
    # of both).
    pattern_confidence: float = 1.96

    # --- Phase 2: AI packaging enrichment (opt-in — costs LLM) ---
    # Model overrides for the enrichment calls. EMPTY means "use the selected
    # provider's configured model" (see llm_provider above), which keeps the
    # default multi-provider behavior intact. Set one to spend less — e.g.
    # PRIMOCUT_ENRICH_TITLE_MODEL=claude-haiku-4-5 for the title pass. A model id
    # is provider-specific, so an override is only valid for the provider in use;
    # the vision model must support image input.
    enrich_title_model: str = ""
    enrich_vision_model: str = ""
    enrich_comments_model: str = ""

    # --- Phase 3: clip rendering (FFmpeg/NVENC) ---
    # Base dir for the rendered clips. Unlike the audio (a throwaway temp dir),
    # clips are DELIVERABLES: they are laid out under
    # `<clips_dir>/<video_id>/<cut_id>/NNN-slug.mp4`, never swept by any cleanup,
    # and removed only by an explicit delete.
    clips_dir: Path = Path("clips")
    # Only the SECTION around an item is downloaded (an ephemeral intermediate,
    # like the audio). This margin is added on each side because yt-dlp cuts the
    # download at keyframes: the margin guarantees the exact item range sits
    # inside the fetched section, so the FFmpeg re-encode can trim to it exactly.
    render_section_margin_s: float = 15.0
    # Default fine-cut encoder. "nvenc" (h264_nvenc) is the default — a real
    # re-encode gives exact start/end boundaries; on an NVENC failure the job
    # falls back to "libx264" (slower, logged as a warning). `--fast-copy`
    # (`-c copy`) is opt-in per render and documented as keyframe-imprecise.
    render_encoder: str = "nvenc"  # "nvenc" | "libx264"
    render_nvenc_cq: int = 19  # NVENC constant-quality level (lower = better)
    render_x264_crf: int = 18  # libx264 CRF (fallback encoder)
    render_audio_bitrate: str = "192k"

    # --- Phase 3: cut export (packaging a cut into a deliverable bundle) ---
    # Base dir for the export packages. Kept SEPARATE from clips_dir: clips are the
    # per-item deliverables, an export is the assembled bundle (clips copied in
    # order + per-clip SRT + concat master + manifests, optionally zipped). Export
    # NEVER mutates the originals under clips_dir. Layout:
    # `<exports_dir>/<video_id>/<cut_id>/<cut-slug>-<timestamp>/` (history kept),
    # with a sibling `<cut-slug>-<timestamp>.zip`.
    exports_dir: Path = Path("exports")
    # Target height for `--upscale-1440p`: the master is re-encoded to this height
    # (width auto, even) via NVENC scale so YouTube assigns better codecs to the
    # >=1440p upload. Only the master is upscaled; the individual clips stay at the
    # original resolution (they are editing sources).
    export_upscale_height: int = 1440

    # --- Phase 3: surgical WhisperX boundary alignment (isolated venv) ---
    # The boundary refinement runs in a SEPARATE venv: whisperx + torch 2.8 +
    # pyannote 4 conflict with the validated worker venv (they downgrade
    # huggingface-hub and graft a torch/CUDA stack over faster-whisper's runtime),
    # so the render worker shells out to `align_python` (the align venv's
    # interpreter) for the word-level pass. Unset/missing → alignment is
    # unavailable and the render keeps the item's original [start, end]:
    # refinement is an improvement, never a blocker (silent fallback).
    align_python: str = ""
    # Move a boundary only to the nearest word/sentence edge WITHIN this tolerance;
    # a nearest edge farther than this keeps the original timestamp (never cuts
    # mid-word). Kept <= render_section_margin_s so the alignment window always
    # sits inside the already-downloaded section (no extra download).
    align_tolerance_s: float = 3.0
    # Hard cap on the alignment subprocess; exceeding it → silent fallback.
    align_timeout_s: float = 60.0
    # Optional wav2vec2 alignment model override (PT-BR); empty → whisperx picks
    # its default `pt` align model.
    align_model: str = ""

    # --- Phase 3: AI packaging of a cut (opt-in — costs LLM) ---
    # Per-clip title/thumbnail-brief generation and frame ranking, driven by the
    # niche's Phase 2 patterns. Everything here is OPT-IN (guideline 9): the LLM
    # passes run only on an explicit user action; frame EXTRACTION is deterministic
    # (FFmpeg only, never an LLM). Results are paid for once per clip by a content
    # hash and regenerated only on an explicit request.
    #
    # Default number of candidate titles a generation proposes (selectable per run
    # in the UI). Each proposal cites which measured patterns it applies.
    packaging_titles_k: int = 5
    # Default number of candidate frames extracted per clip (selectable per run).
    packaging_frames_per_clip: int = 3
    # FFmpeg scene-detection threshold for the deterministic frame extraction: a
    # frame is a scene-change candidate when its `scene` score exceeds this. When
    # the clip yields fewer scene changes than requested, evenly-spaced samples fill
    # the rest — so a flat clip still returns the requested count.
    packaging_scene_threshold: float = 0.30
    # Which patterns feed the title/brief prompt. A boolean attribute counts as a
    # niche CONVENTION (a requirement/table-stake, not a differential) when its
    # frequency across the WHOLE niche is at least this — such attributes never
    # separate outliers from control (their Wilson intervals overlap high), so the
    # differential guard would drop them, yet they are what the niche requires.
    packaging_convention_floor: float = 0.85
    # A carries-value pattern (the "name that carries", a recurring topic word, a
    # thumbnail-text token) is reported only when it appears in at least this many
    # OUTLIER videos AND at a lift over the control of at least
    # `packaging_carries_value_min_lift`. This is the never-invent-a-pattern guard
    # for non-boolean values, mirroring the Wilson guard for booleans.
    packaging_carries_value_min_videos: int = 3
    packaging_carries_value_min_lift: float = 1.5
    # How many real outlier examples (titles + enriched attributes) the prompt
    # carries as references the model can cite.
    packaging_examples: int = 6
    # Which niche format's patterns drive packaging by default (clips are usually
    # posted as long-form on the destination channels). "long" | "short".
    packaging_format: str = "long"
    # Model overrides for the packaging LLM passes. EMPTY means "use the selected
    # provider's configured model" (see llm_provider). The frame-ranking pass needs
    # a VISION-capable model. A model id is provider-specific.
    packaging_titles_model: str = ""
    packaging_brief_model: str = ""
    packaging_frame_rank_model: str = ""
    packaging_audience_model: str = ""


def get_settings() -> Settings:
    """Instantiate the settings reading environment/.env at call time."""
    return Settings()
