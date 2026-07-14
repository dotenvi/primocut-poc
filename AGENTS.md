# AGENTS.md

Canonical agent instructions for PRIMOCUT. Every line here is loaded into context
each request — keep it dense and current. On any contradiction with the code, the
**code wins**; then fix the docs.

## What this is

PRIMOCUT turns a **YouTube live URL** into a **navigable report**: full transcript
(clickable timestamps), per-minute chat activity (z-score peaks as clip candidates),
and opt-in LLM macro topics. Everything derives from **real** timestamps, so every
`&t=SECONDS` link lands exactly on the moment.

- Product source of truth: `docs/PRIMOCUT.md`. Dev source of truth (roadmap, state,
  resilience, decision log): `docs/DEVELOPMENT.md`. Full architecture: `README.md`.

## Architecture (the one rule that shapes everything)

There is **one shared pipeline core**, `run_stages` in `src/primocut/pipeline.py`
(Metadata → audio → transcription → chat → analysis). It is the **only** place the
stage order lives. **Never duplicate stage logic** — CLI and worker both reuse the
core, differing only by injected `Store` + `ProgressReporter`.

- **CLI** (`cli.py`, `primocut run`): in-process, `FileStore` (JSON) + `LogReporter`,
  writes a Markdown report. No Redis, no SQLite, no worker.
- **Web app**: `primocut serve` (FastAPI in `api/`, also serves the built SPA) +
  `primocut worker` (RQ job `process_video` in `worker/jobs.py`, `DbStore` +
  `DbReporter`). SQLite (`db/`) is the **sole** channel between API and worker; the
  UI polls `/api` every ~2 s (no SSE/WebSocket). Transcription is serialized by a
  Redis GPU lock (one at a time). WebUI (`frontend/`, Svelte 5 + Vite) owns
  presentation only — no business logic, never touches DB/queue/pipeline directly.

## Golden rule (analysis)

The LLM only ever returns segment **indices**, **never timestamps**. Index→time
resolution lives in `models.py` (`resolve_span` clamps to real segments). Do not let
any prompt or parser trust model-emitted times.

## Conventions

- **Language**: everything committed to the repo is **English** — code, comments,
  docstrings, user-visible strings (UI/reports/logs/errors), and **all `.md` docs**,
  no exception. Talk to the user in **Portuguese** (CLI chat only). A task asking for
  Portuguese docs is a mistake — confirm, don't write PT into an artifact.
- **Tooling**: native Python only — `python -m venv` + `pip` + `requirements*.txt`.
  **No uv/poetry** without explicit authorization. WSL2 nuance: the venv was created
  `--without-pip`; install with `/usr/bin/python3 -m pip --python .venv/bin/python …`
  (the venv's own python has no pip). See README §5.1.
- **Tests**: run `pytest` yourself after changes that need validation — don't ask.
  `source .venv/bin/activate && pytest -q`. The suite needs **no GPU/network** (fakes
  + inline execution). Work TDD: write the failing test first.
- **Commits**: commit **directly to `main`** — no branch/PR unless explicitly asked.
  Conventional Commits, English, with a `Co-authored-by` trailer. When asked for a
  commit *list*: chronological build order, tests-first, and output both the full
  `git add …` and `git commit -m "…"` commands.
- **Versioning**: SemVer 2.0.0, currently **0.x** on purpose (public API = CLI,
  Web API, env vars — treated as unstable). `feat`→minor, `fix`→patch, breaking→minor
  while in 0.x. Keep a Changelog format; version unified across `pyproject.toml`,
  `src/primocut/__init__.py`, `frontend/package.json`.

## Gotchas

- No CPU fallback for transcription (by decision). No linter/formatter configured yet.
- Audio is ephemeral (temp dir, discarded per run/job) — nothing to clean up.
- `primocut doctor` validates GPU/CUDA, FFmpeg, yt-dlp, provider key, Redis, DB.
