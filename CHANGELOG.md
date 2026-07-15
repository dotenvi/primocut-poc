# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is in the `0.y.z` range, the public API is considered
unstable: breaking changes may land in any minor release.

## [Unreleased]

### Changed

- **The dashboards never blend Shorts with long-form** — Shorts and long-form
  monetise differently, target different audiences and are different formats, so
  a figure computed across both describes neither. The outlier score already used
  per-format baselines, but the **engagement medians were computed over every
  video mixed**, and the outlier/pattern/resurgence populations were reported as
  one list. Every one of them is now split by format, and the UI picks one with a
  Long-form/Shorts tab.
- **BREAKING (Web API)** — in `GET /api/channels/{cid}/dashboard`, `engagement`,
  `outliers` and `resurgences` are now objects keyed `"long"`/`"short"` instead of
  a flat object/list; in `GET /api/projects/{pid}/dashboard`, the same applies to
  `outliers`, `patterns` and `resurgences`.
- **Patterns clear the minimum-occurrence guard per format**, so a format with too
  few definitive outliers now reports no pattern instead of borrowing the other
  format's support.

### Fixed

- **Shorts longer than 60s are no longer classified as long-form** — the
  `is_short` duration heuristic capped at 60 seconds, but YouTube has allowed
  Shorts of up to 3 minutes since October 2024, so every Short in the 61-180s
  range was counted as long-form — shrinking the Shorts baseline sample and
  polluting the long-form one. `PRIMOCUT_YOUTUBE_SHORT_MAX_SECONDS` now
  defaults to `180`.
  Channels backfilled before this lands keep their old `is_short` values until
  they are re-collected (the flag is written at collection time).

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

[Unreleased]: https://github.com/dotenvi/primocut/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/dotenvi/primocut/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/dotenvi/primocut/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dotenvi/primocut/releases/tag/v0.1.0
