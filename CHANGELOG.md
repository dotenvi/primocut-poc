# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is in the `0.y.z` range, the public API is considered
unstable: breaking changes may land in any minor release.

## [Unreleased]

## [0.3.0] - 2026-07-19

A niche-first rebuild: the interface is reorganised around global events and
per-project patterns, the outlier metric moves from fixed multiples to
per-channel percentile bands, and an append-only event log powers a cross-project
Radar. Several web-API, config and metrics surfaces change or disappear — see
Changed and Removed. In 0.x these breaking changes ship in a minor by policy.

### Added

- **A new navigation architecture: events are global, patterns are per project.**
  **Radar** (a cross-project feed of what happened in the niches, with a "Needs
  you" band, a per-project filter and mark-as-seen) and **Activity** (the system
  log) now sit above the projects; each project contains **Lives**, **Niche** and
  **Settings**. Selecting a project is a route, not a dropdown, and every
  destination is a linkable hash URL with working back/forward.
- **An append-only event log, surfaced as Radar and Activity.** The snapshot
  cycle emits `outlier_crossed`, packaging-change and cycle events into an
  append-only `events` table guarded against flapping by a per-video band memory
  (`last_band_emitted`); the Radar renders them as alerts, the Activity feed lists
  them, and each carries a seen state. Comment collection is now driven by
  `outlier_crossed` events rather than a scan.
- **Cut room — the bridge between the two halves of the product.** A live's page
  pairs the player and a pipeline stage rail with a **Packaging kit** fed by the
  niche's own patterns (attributes with real lift, reference outliers, the
  audience's explicit requests), and chat peaks now join the topic list as
  first-class clip candidates.
- **Lives live inside a project, and a niche can track your own channels** —
  `primocut lives add/list` operate within a project, own channels sit alongside
  the competitors, and a project is redefined as a niche.
- **"Channel spread" distribution strip**, one render shared by the Radar cards
  and the competitor video detail, backed by a single new endpoint
  `GET /api/channels/{cid}/distribution?format=` (one call per page) and its batch
  form `GET /api/channels/distribution?channel_ids=&format=` (one call per Radar
  page — bounded by channels, never an N+1 or a snapshot scan).
- **The niche enrich buttons show the cost before the click** ("Enrich titles ·
  268 new"), backed by `GET /api/projects/{pid}/enrich/preview?kind=`, which
  reports how many videos are new versus already cached.
- **Tailwind CSS build** (Manrope + DM Mono); the design tokens are ported
  verbatim from the approved prototype.
- **Competitor thumbnails render in the Radar feed**, with a backfill that fills
  the thumbnail URL on legacy rows.
- `GET /api/projects` now carries `live_count`; `GET /api/projects/{pid}` carries
  a read-only `band_config`; the event feed resolves each event's `title` and
  `channel_title` on read, so a Radar card names the video and channel without the
  payload having to store them.

### Changed

- **Outliers are classified by per-channel, per-format percentile bands, and
  patterns are guarded by a Wilson interval.** The fixed 3x/6x/10x classifier is
  replaced by the channel's own p90/p97/p99 (nearest-rank, over the same rolling
  window as the median) mapped to relevant/strong/exceptional, so ~10% of a
  channel lands in the tail instead of ~30%. The median multiple is still computed
  and shown ("20.6x") but no longer classifies. Patterns now compare the banded
  outliers against a band-less mature control and display only when the two 95%
  Wilson intervals do not overlap (positive pattern or anti-pattern); there is no
  minimum-occurrence floor — the interval self-regulates.
  **BREAKING (Web API)** — `OutlierScore`/payload fields are renamed
  (`score`→`median_multiple`, `bracket`→`band` with `band_basis`/`percentile`/
  `band_cutoffs`), the status `insufficient_data`→`insufficient_sample`, and the
  pattern payload now carries `freq_outlier`/`ci_outlier`/`freq_control`/
  `ci_control`/`n_outlier`/`n_control`/`verdict`.
- **The dashboards never blend Shorts with long-form.** Shorts and long-form
  monetise differently, target different audiences and are different formats, so a
  figure computed across both describes neither. Engagement medians and the
  outlier, pattern and resurgence populations are now each split by format, and
  the UI picks one with a Long-form/Shorts tab. Patterns clear the
  minimum-occurrence guard per format, so a thin format reports nothing rather
  than borrowing the other's support.
  **BREAKING (Web API)** — in `GET /api/channels/{cid}/dashboard`, `engagement`,
  `outliers` and `resurgences` are now objects keyed `"long"`/`"short"` instead of
  a flat object/list; in `GET /api/projects/{pid}/dashboard`, the same applies to
  `outliers`, `patterns` and `resurgences`.
- **"Competitors" is renamed "Niche"** (tabs Patterns/Outliers/Channels),
  "Submit" becomes a **+ New live** button inside Lives, and the per-channel
  dashboard is now a drill-down of Niche › Channels rather than a top-level
  destination.
- **The `title`/`thumbnail` enrichment batch now targets every mature video** —
  both sides the pattern comparison reads — via `service.enrichment_target_ids`;
  provisional and insufficient-sample videos still stay out. `comments` continue
  to target only the definitive outliers. `POST /api/projects/{pid}/enrich` now
  returns `{kind, new, cached, total, rq_job_id}` and dispatches only the cache
  misses.
- **The workspace UI is refined** — a wider canvas that keeps reading views
  comfortable, a paginated Radar feed with per-section pagers, the Cut room's
  pipeline log beside the player, a fixed sidebar/footer with a working chevron
  toggle, and larger per-stage status text.

### Deprecated

- **The fixed-multiple outlier basis is demoted to a legacy `band_basis`.**
  Percentile bands are the default; the fixed multiples remain available only as a
  configured legacy basis and no longer classify on their own.

### Removed

- **The top-level `/api/videos*` surface is removed** — the frontend and clients
  now target the project-scoped lives endpoints `/api/projects/{pid}/lives/...`.
  **BREAKING (Web API).**
- **The settings `outlier_brackets`, `pattern_min_occurrence` and
  `pattern_outlier_bracket` are removed**, superseded by the percentile-band
  configuration (`band_basis`, `band_cutoffs_*`). **BREAKING (config).**
- **The project dropdown** (the project now lives in the URL), the **hand-rolled
  stylesheet** and the **Tailwind Play CDN** are gone.

### Fixed

- **Niche › Patterns (and the Cut Room Packaging kit it feeds) no longer stay
  blank for the LLM sources.** The conditional distributions compare two groups —
  the banded outliers against a band-less control — but the enrichment batch only
  targeted the outliers, so `title_llm`/`thumbnail_llm` had a control of N=0 and
  every Wilson comparison had nothing to separate. `syntactic` was unaffected
  because the snapshot cycle fills it for the whole population.
- **Shorts longer than 60s are no longer classified as long-form** — the
  `is_short` duration heuristic capped at 60 seconds, but YouTube has allowed
  Shorts of up to 3 minutes since October 2024, so every Short in the 61-180s
  range was counted as long-form — shrinking the Shorts baseline sample and
  polluting the long-form one. `PRIMOCUT_YOUTUBE_SHORT_MAX_SECONDS` now defaults
  to `180`. Channels backfilled before this lands keep their old `is_short` values
  until they are re-collected (the flag is written at collection time).
- **Legacy databases missing `competitor_videos.last_band_emitted` are migrated**
  so the anti-flapping band guard works on pre-existing rows.

## [0.2.1] - 2026-07-15

Fixes a competitor channel registration that silently never collected when the
queue broker was unreachable.

### Fixed

- **A failed backfill enqueue is no longer silent** — the channel row is
  committed before the job is enqueued, so a broker that is down left the
  channel in its initial `pending` state, reporting "Collecting uploads… 0/?"
  while nothing collected and nothing retried. The failure is now recorded on
  the channel (`backfill_state="error"`) and reported to the caller.
- **A channel whose collection never ran is no longer stranded** — global dedup
  keyed off the channel's mere existence, and detaching preserves the row, so
  re-adding a channel left in `pending`/`error` reported a collection that never
  happened. Dedup now skips only a channel that actually finished collecting.
- **`POST /api/projects/{id}/channels` answers 503** when the queue is
  unreachable, instead of an unhandled 500.
- **`primocut competitors add`** reports an unreachable queue instead of a
  traceback, and no longer claims "no new collection" when it re-enqueues one.

## [0.2.0] - 2026-07-15

Phase 2 — competitor channel modeling. Adds projects, continuous collection from
the YouTube Data API v3, a deterministic metrics engine, and opt-in LLM
enrichment of packaging and comments. Requires a YouTube Data API v3 key.

### Added

- **Projects and competitor channels** — competitor channels are global entities
  linked to projects via M:N, so a channel shared by two projects is collected
  once and its metrics computed once, while niche-level reports stay scoped per
  project. Detaching keeps all history; a channel orphaned from every project is
  deactivated, never deleted.
- **YouTube client** — quota-accounted YouTube Data API v3 client (public data
  only): channel resolution from URL/@handle/ID, video and channel statistics,
  paged top comments, and thumbnail content hashing.
- **Continuous collection** — backfill of the last 120 uploads per new channel,
  then snapshots daily for every video and every 6h for videos younger than 7
  days, with packaging-change detection by title string and thumbnail content
  hash. Runs as a scheduler process (`primocut scheduler`) over rq-scheduler.
- **Metrics engine** — deterministic and LLM-free: outlier score against the
  channel median with separate Shorts/long-form baselines, configurable brackets,
  and provisional/insufficient-data guards; views-per-hour from snapshot deltas;
  resurgence alerts; packaging before/after impact; engagement and cadence; and
  per-project conditional distributions of packaging attributes behind a
  minimum-occurrence guard.
- **Metrics dashboards** (Web API) — `GET /api/projects/{id}/dashboard`,
  `GET /api/channels/{id}/dashboard` and `GET /api/channels/{id}/videos/{id}`,
  assembled on read with no stored scores.
- **AI packaging enrichment** (opt-in — costs LLM) — structured thumbnail
  attributes via a vision model and title semantics via a cheap model, cached by
  content hash and shared across projects, triggered per video or as a batch over
  a project's definitive outliers. The model observes; the code derives the
  booleans the pattern math reads.
- **Comments** — top-liked comments collected automatically once a video is a
  definitive outlier (quota only, no LLM); the synthesis pass is manual and
  reports recurring themes, sentiment and the audience's explicit requests, which
  the project dashboard aggregates into a ranked demand list ordered by how many
  videos asked. Every quote shown is a real comment: the model returns indices,
  never text.
- **CLI** — `projects`, `competitors` and `metrics` sub-apps, plus `scheduler`.
- **Web UI** (Svelte) — competitors section with the niche, channel and video
  dashboards, project switcher, outlier tables, sparklines, and the opt-in AI
  panels. Methodology guards are surfaced rather than hidden.
- **Configuration** — YouTube key and collection windows, the two snapshot cron
  schedules, the metrics-engine thresholds, and per-pass model overrides.

### Changed

- **`primocut doctor`** — adds an eighth check that validates
  `PRIMOCUT_YOUTUBE_API_KEY` with a real 1-unit call. It now **fails when the key
  is unset**, consistent with the LLM provider key.
- **LLM layer** — `select()` accepts images and a per-call model override for the
  enrichment passes. The topics path is unchanged.

## [0.1.0] - 2026-07-14

First tagged release. Establishes the end-to-end pipeline and the multi-layer
architecture (CLI, worker, jobs, web API and web UI).

### Added

- **Pipeline** — end-to-end orchestration turning a live URL into a Markdown
  report: audio download → transcription → live-chat signals → LLM analysis,
  with graceful degradation when a stage fails.
- **Download** — audio extraction via yt-dlp/FFmpeg, with retry and
  `player_client` fallback.
- **Transcription** — `TranscriptionEngine` interface backed by faster-whisper,
  with `compute_type` fallback and streamed progress.
- **Live chat** — chat downloader with `yt-dlp --live-chat` fallback and
  z-score-based signal detection.
- **Analysis** — macro topics and cut moments extracted via LLM tool use, using
  windowing with per-window retry.
- **Report** — Markdown report with clickable timestamps and a degradation
  section; per-stage timing recorded in the job stage history.
- **CLI** (Typer) — `run`, `serve`, `worker`, `cleanup`, `doctor` and `models`
  commands; `doctor` diagnoses GPU/CUDA, FFmpeg, yt-dlp, API key and SDK, plus
  Redis/DB checks.
- **Web API** (FastAPI) — HTTP endpoints and a report service.
- **Worker** — RQ jobs on a Redis queue with scheduled cleanup.
- **Persistence** — SQLite layer (models, session, DAO, migrations).
- **Web UI** (Svelte) — video list and report page with a tabbed layout,
  per-stage pipeline timing, on-demand LLM topic generation and a vertical
  navigation sidebar.
- **Model catalog** — OpenRouter model catalog filtered by tool-use support.
- **Configuration** (pydantic-settings) — settings for Redis, database, GPU lock
  and job timeout.
- **Resilience** — retry with backoff and transient-error classification.

[Unreleased]: https://github.com/dotenvi/primocut/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/dotenvi/primocut/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/dotenvi/primocut/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/dotenvi/primocut/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dotenvi/primocut/releases/tag/v0.1.0
