# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the project is in the `0.y.z` range, the public API is considered
unstable: breaking changes may land in any minor release.

## [Unreleased]

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

[Unreleased]: https://github.com/dotenvi/primocut/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dotenvi/primocut/releases/tag/v0.1.0
