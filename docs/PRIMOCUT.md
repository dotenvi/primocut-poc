# PRIMOCUT

**Pri**nt **Mo**ney with **Cut** — automation of YouTube clip channels.

> This is the **product** source of truth (the what and why) and it **evolves in place**: new
> phases update this file instead of adding new markdown documents to `docs/`. For how it is
> built and run — roadmap detail, current state, run guide, architecture, resilience and the
> decision log — see [`DEVELOPMENT.md`](./DEVELOPMENT.md).

## Vision

PRIMOCUT attacks the two most expensive problems of running YouTube clip channels:

1. **Finding what to clip in your own material.** The application receives the URL of a YouTube live (podcast, stream), downloads the audio and the chat replay, transcribes it with timestamps and, on demand, uses AI to generate an index of the topics that were discussed. The user reads the report — instead of watching 3-4 hours of video — and identifies the passages they want to turn into clips.
2. **Knowing what works in the niche.** Competitor channels, grouped into projects (one project per clip channel/niche the user runs), are continuously monitored through public data. The system surfaces outlier videos, packaging patterns (title/thumbnail), posting strategy and audience reaction, so editorial decisions are driven by evidence instead of intuition.

## Live report (Phases 0–1 — delivered)

From a URL, a navigable report containing:

- Full searchable transcript with clickable timestamps and an embedded YouTube player — clicking a topic or a "Watch" action seeks the player to the right moment (external `&t=SECONDS` links remain as fallback).
- Chart of chat activity throughout the live (message peaks = clip candidates), with 🔥 markers on topics that coincide with peaks.
- Macro-topics in chronological order (**opt-in — costs LLM**): title, short summary, start/end timestamp, clip-potential score. Topic count is unbounded: one topic per genuinely distinct subject.
- CLI equivalent (`primocut run`) producing the same report as Markdown + JSON.

## Competitor channel modeling (Phase 2 — delivered)

Given competitor channels supplied by the user (no niche-wide discovery), grouped into **projects**:

- **Projects**: competitor channels are global entities linked to projects via M:N. The same channel in two projects is collected once; per-channel metrics are computed once; niche-level reports (rankings, pattern distributions) are scoped per project so one niche never contaminates another. Detaching a channel keeps its history; orphaned channels stop being collected until re-linked.
- **Continuous collection** (YouTube Data API v3): backfill of the last 120 videos per new channel, which also records that channel's first snapshot so the dashboards are populated right away; snapshots daily for all videos and every 6h for videos younger than 7 days (views, likes, comment counts, channel stats); packaging-change detection by comparing title strings and thumbnail **content hashes** between snapshots; quota accounting on every cycle.
- **Metrics engine** (deterministic, no LLM cost): outlier classification into bands (relevant/strong/exceptional) over a rolling window, with separate rulers for Shorts vs long-form and provisional scores under 7 days of age. The **default basis is percentile** — the band cutoffs are the channel's own p90/p97/p99 (nearest-rank), so ~10% of a channel lands in the tail; the readable **median multiple** ("20.6× the median") is still shown but no longer classifies. Fixed multiples (3x/6x/10x) are a demoted **legacy basis**: on the skewed YouTube view distribution 3× the median lands around the channel's p72, marking ~30% of videos as outliers and — because that population then contaminated the pattern denominator — flattening every lift toward 1.0. Also: views-per-hour from snapshot deltas; resurgence alerts; packaging-change before/after impact; engagement rates; cadence; per-project conditional distributions of title/thumbnail attributes comparing outliers against a censored **control** group (band-less, mature, sufficient-baseline; provisional videos excluded from both), gated by **non-overlap of 95% Wilson intervals** in either direction (positive pattern or anti-pattern).
- **AI enrichment (opt-in — costs LLM)**: structured thumbnail attributes via vision model and title semantics via cheap model, cached by content hash and shared across projects. **Comments**: collection is automatic (top-liked comments fetched when a video is a definitive outlier, stored raw, quota-accounted); only the LLM synthesis pass is triggered manually — recurring themes, overall sentiment and the audience's **explicit requests** (guests, subjects, formats), which the project dashboard aggregates into a ranked demand list for the niche. Every quote it shows is a real comment: the model selects comment indices, never text.
- Private YouTube Analytics data (CTR, retention, impressions) is unavailable for third-party channels and is not modeled.

## Execution context

- Runs 100% locally: Debian via WSL2, single-user, no auth.
- GPU: NVIDIA RTX 3070 Ti (8 GB VRAM) via CUDA on WSL2. The driver lives on Windows; inside WSL use CUDA/cuDNN libraries via pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). Validate with `nvidia-smi`.
- PT-BR lives only (the distil Whisper models are English-only — do not use).
- Competitor modeling requires a YouTube Data API v3 key in `PRIMOCUT_YOUTUBE_API_KEY` (free tier: 10,000 units/day — far above the tracked-channel needs).

## Architecture

```
[Web UI (Svelte)] → [FastAPI] → [Queue (RQ + Redis)] → Workers:
                                              1. Audio download (yt-dlp, audio only)
                                              2. Chat replay download (chat-downloader)
                                              3. Transcription (faster-whisper, GPU,
                                                 distributed GPU lock in Redis)
                                              4. Analysis (LLM → topics + timestamps,
                                                 cross-referencing chat signals)

[Scheduler (rq-scheduler)] → Collector jobs → YouTube Data API v3
        (audio cleanup + competitor snapshots: daily / 6h; global,
         deduplicated across projects; per-channel failure isolation)

[SQLite] ← metadata, jobs, transcripts, topics, chat signals,
           projects, competitor channels/videos, snapshots,
           packaging changes, attributes, comments, enrichments
[Temp dir] ← audio files (per job, discarded when the job ends)
```

## Stack (decided)

| Layer | Technology |
|---|---|
| Audio download | yt-dlp (extract audio only, normalize with FFmpeg to 16kHz mono) |
| Chat download | chat-downloader (fallback: yt-dlp live_chat) |
| Transcription | faster-whisper, model `large-v3-turbo`, compute on GPU |
| Analysis/topics + enrichment | LLM via API, multi-provider: Anthropic (default), OpenAI, OpenRouter — forced tool use |
| Competitor data | YouTube Data API v3 (API key, public data only, quota-accounted) |
| API | FastAPI |
| Queue / scheduling | RQ + Redis; rq-scheduler for recurring jobs (cleanup, snapshots) |
| Database | SQLite |
| Frontend | Svelte (SPA compiled with Vite), served as static files by FastAPI |
| Dependencies | Standard Python: venv + pip + requirements.txt (no uv/poetry) |

## Data model

Live report:

- **Video**: id, url, title, channel, duration, status (queued/downloading/transcribing/analyzing/done/error)
- **Transcript**: video_id, segments [{start, end, text}], language, model used
- **ChatSignal**: video_id, time series [{minute, msgs, laughs, emotes}], detected peaks [{ts, intensity}]
- **Topic**: video_id, title, summary, ts_start, ts_end, score
- **Job**: video_id, stage, progress, error

Competitor modeling:

- **Project**: id, name, description, active
- **CompetitorChannel** (global): channel_id, handle, title, uploads_playlist_id, active — linked to projects via **ProjectChannel** (M:N)
- **ChannelSnapshot**: channel_id, captured_at, subscribers, total_views, video_count
- **CompetitorVideo**: channel_id, video_id, title, published_at, duration_s, is_short, thumbnail_hash, comments_state
- **VideoSnapshot**: video_id, captured_at, views, likes, comments
- **PackagingChange**: video_id, field (title|thumbnail), old/new value, detected_at, vph_before/vph_after
- **VideoAttributes**: video_id, source (syntactic|title_llm|thumbnail_llm), attributes JSON, content_hash
- **VideoComments**: video_id, comment_id, text, like_count, author, fetched_at (top comments of outliers, collected automatically)
- **VideoEnrichment**: video_id, kind (comment_synthesis), payload JSON, content_hash — kept apart from VideoAttributes so a narrative synthesis never counts as a packaging pattern

Outlier scores and VPH are computed on read (over snapshots), never stored.

## Technical guidelines

1. **Project language is English**: every comment, docstring, identifier, log/error message and user-facing string is written in English, and so is the product documentation. Any new code follows the same rule.
2. **TranscriptionEngine as an interface**: the transcription worker must sit behind an abstraction so faster-whisper can be swapped for WhisperX (Phase 3) without touching the rest of the system.
3. **Timestamps are never invented**: the LLM analysis must reference exclusively timestamps that exist in the transcript. Structure the prompt/data so the LLM selects segments, rather than generating numbers.
4. **Long transcripts**: 3-4h lives exceed the LLM's useful context — process them in overlapping windows and consolidate the topics.
5. **Chat is an optional signal**: not every live preserves the chat replay; its absence must not block the pipeline. Peak detection by message density per minute (z-score) and reaction markers (laughs "KKKK", emotes).
6. **Audio is ephemeral**: the audio is written to a throwaway temp dir and discarded when the run/job ends; transcripts, topics and chat signals are permanent (database in the web app, `--output` JSON caches in the CLI).
7. **Asynchronous jobs**: no heavy processing inside an HTTP request; everything goes through the queue with persisted status/progress.
8. **yt-dlp kept up to date**: it breaks frequently due to YouTube changes; pin an easy update process.
9. **Anything that costs LLM is opt-in**: topic generation, packaging enrichment and comment synthesis run only on explicit user action. Deterministic collection (snapshots, automatic comment fetching of outliers) runs on schedule, quota-accounted.
10. **Metrics correctness is non-negotiable**: median baselines (never mean), separate Shorts/long-form rulers, percentile bands by default (fixed multiples are legacy), VPH exclusively from snapshot deltas, provisional scores before maturity, a censored control group (never the outlier-contaminated population) as the pattern denominator, and non-overlap of 95% Wilson intervals — not a raw count floor — before reporting any pattern.
11. **Multi-project semantics**: channels are global, collection is deduplicated across projects, per-channel metrics are computed once, niche-level reports are always project-scoped, and detaching never deletes data.
12. **Degrade, don't abort** (see DEVELOPMENT.md for the full resilience catalog): only audio and transcription are essential to the live pipeline; collector cycles isolate per-channel failures and never crash the scheduler.

## Roadmap

- **Phase 0 — CLI spike ✅:** command-line script: URL → audio → transcription → chat → topics in Markdown. Validated GPU on WSL2, macro-topic segmentation quality and chat peak detection.
- **Phase 1 — MVP ✅:** queue + database + web UI + navigable report with embedded player.
- **Phase 2 — Competitor channel modeling ✅:** as specified in the section above; the six delivery steps and their approved decisions live in DEVELOPMENT.md.
- **Phase 3 — Assisted cutting (next):** automatic clip generation with FFmpeg; evaluate WhisperX (word-level timestamps + diarization); may leverage niche intelligence from Phase 2 to prioritize what to cut.
- **Phase 4 — Publishing:** AI titles/descriptions, vertical format, upload via the YouTube API, multi-user.
