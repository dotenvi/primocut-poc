# PRIMOCUT — Development

> Source of truth for **development**: current state, run guide, architecture, resilience,
> roadmap and the decision log (technical and business decisions, with their reasons).
> It is fed continuously — see the [Update convention](#7-update-convention).
>
> Source of truth for the **product** (the what and why): [`PRIMOCUT.md`](./PRIMOCUT.md).

Contents:
1. [Current state](#1-current-state)
2. [How to run](#2-how-to-run)
3. [Architecture](#3-architecture)
4. [Resilience](#4-resilience)
5. [Roadmap](#5-roadmap)
6. [Decision log](#6-decision-log)
7. [Update convention](#7-update-convention)

---

## 1. Current state

PRIMOCUT takes the URL of a YouTube live and produces a **navigable report** with the
transcript, chat activity and (on demand) an index of macro topics. There are two front
ends over the **same pipeline core** (no logic is rewritten between them):

- **CLI** (`primocut`): commands `doctor` (environment diagnostics), `run` (end-to-end
  pipeline → Markdown report), `models` (lists OpenRouter models with tool-use support,
  including free ones), `serve` and `worker`.
- **Local web app** (`primocut serve` + `primocut worker`): a Svelte SPA served by FastAPI,
  an RQ+Redis queue and a SQLite database. You submit a URL, follow the live status
  (polling) and open the report in the browser.

The pipeline runs, in order: **audio (yt-dlp → FFmpeg 16 kHz mono) → chat replay (optional)
→ transcription (faster-whisper `large-v3-turbo`, GPU) → analysis (LLM, macro topics)**.

What the report delivers today:
- **Full, searchable transcript**, with clickable timestamps
  (`https://youtube.com/watch?v=ID&t=SECONDS`).
- **Chat activity**: per-minute series, sparkline/table and z-score peaks (clip candidates);
  a 🔥 marker on topics that coincide with peaks.
- **Macro topics** (title, summary, start/end, score) — **opt-in**: they are not generated
  automatically (they cost an LLM call). Generate them on demand via the
  **"Generate topics with AI"** button in the report, or with `--create-topics` on the CLI
  `run`.
- **Embedded YouTube player**: a topic's **Watch** button seeks the player.

Code state: the pipeline runs end to end (validated on a real ~33-min live — transcription of
914 segments in ~82 s on the RTX 3070 Ti). Test suite green, with no GPU/network needed
(fakes + inline execution).

> **Product vs. implementation nuance:** `PRIMOCUT.md` describes the topic index as the core
> of the MVP; today it is **opt-in** by a cost decision (see [2026-07-13 · Opt-in macro topic
> generation](#2026-07-13--opt-in-macro-topic-generation)). The rest of the report
> (transcript, chat) is always generated.

---

## 2. How to run

All commands assume the project directory (`~/projects/primocut`).

### 2.1 Environment setup (native Python: venv + pip)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime + pytest
pip install -e .                      # registers the `primocut` command
```

> **Nuance of this environment (WSL2):** if `python -m venv` fails with
> `ensurepip is not available`, the `python3.13-venv` package is missing
> (`sudo apt install python3.13-venv`). Without sudo, create the venv **without pip** and
> install by pointing the system pip at the venv's interpreter:
> ```bash
> python3 -m venv .venv --without-pip
> /usr/bin/python3 -m pip --python .venv/bin/python install -r requirements-dev.txt
> /usr/bin/python3 -m pip --python .venv/bin/python install --no-deps -e .
> ```
> Important: do **not** call plain `python3` with the venv activated to install — the venv's
> python has no pip (`No module named pip`). The absolute path `/usr/bin/python3` always works.

### 2.2 System and runtime dependencies

```bash
# FFmpeg (system) — needs sudo
sudo apt update && sudo apt install -y ffmpeg

# Heavy libs (audio, transcription, chat) in the venv — use the SYSTEM pip on pip-less WSL2:
/usr/bin/python3 -m pip --python .venv/bin/python install faster-whisper yt-dlp chat-downloader

# (Optional) CUDA via pip, if faster-whisper cannot find cuBLAS/cuDNN on WSL:
/usr/bin/python3 -m pip --python .venv/bin/python install nvidia-cublas-cu12 nvidia-cudnn-cu12

# Web app: all dependencies at once
/usr/bin/python3 -m pip --python .venv/bin/python install -r requirements.txt
```

- **Redis** (queue): `sudo apt install redis-server` **or** `docker run -p 6379:6379 redis`.
- **Node** (UI build): validated with v22.
- **LLM (optional, only for topics):** each provider bills its own API — a consumer
  subscription (Claude Pro/Max, ChatGPT Plus) does **not** grant API access. Copy
  `.env.example` to `.env` and fill in the key of the chosen provider (see
  [2.4](#24-cli-run-and-models)).

### 2.3 Environment diagnostics (run this first)

```bash
primocut doctor
```

Validates **GPU/CUDA (WSL2), FFmpeg, yt-dlp, the selected provider's key, the provider SDK,
Redis and the database**, and prints the fix command for each pending item. Exits with code
`1` while there are pending items.

### 2.4 CLI: `run` and `models`

```bash
# Start with a SHORT live/video to validate quickly (GPU, cost, quality)
# -o is the output DIRECTORY (default: current dir); artifacts (report.md +
# transcript/topics JSON) are exported there. The audio uses a temp dir.
primocut run "https://www.youtube.com/watch?v=<ID>" -o ./out

# Macro topics are opt-in on the CLI too:
primocut run "<url>" -o ./out --create-topics

# Provider/model directly on the command line (without editing .env):
primocut run "<url>" -o ./out --provider openai     --model gpt-4o-mini
primocut run "<url>" -o ./out --provider anthropic  --model claude-haiku-4-5
primocut run "<url>" -o ./out --provider openrouter --model anthropic/claude-3.5-sonnet
```

Supported providers (`SelectionLLM` interface, selected via `PRIMOCUT_LLM_PROVIDER` or
`--provider`/`--model`):

| Provider | Key variable | Default model |
|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | `claude-sonnet-5` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| `openrouter` | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` (`provider/model` slugs) |

The analysis **requires tool use (function calling)**. If the model does not support it, the
pipeline fails with a clear message (`ToolCallError`) instead of producing an empty report.
To find compatible OpenRouter models (including free ones, no balance needed):

```bash
primocut models --free   # free + with tools (e.g. meta-llama/llama-3.3-70b-instruct:free)
primocut models          # all with tools
```

### 2.5 Web app (local production)

```bash
# 1. build the UI (once, or whenever the frontend changes)
cd frontend && npm install && npm run build && cd ..

# 2. Redis up (see 2.2), then in separate terminals:
source .venv/bin/activate
primocut worker      # processes the queue (needs Redis; a GPU for real transcription)
primocut serve       # API + UI at http://127.0.0.1:8000
```

In the browser (`http://127.0.0.1:8000`): submit a URL, follow the status in the list, open
the report (chat activity with peaks, searchable transcript, clickable timestamps, 🔥 on
items that coincide with peaks). Macro topics are **not** generated
automatically — use the **"Generate topics with AI"** button in the topics card (creates or
regenerates on demand). A topic's **Watch** button seeks the embedded player.

Running **N workers is safe by construction**: the light stages (download/chat/analysis) of
different videos overlap, but the GPU lock guarantees **1 transcription at a time**
(see [Architecture](#3-architecture)).

### 2.6 Audio is ephemeral (no cleanup needed)

The normalized audio is written to a throwaway temp dir (`tempfile`) — both in the
CLI (`run_pipeline`) and in the worker (`process_video`) — and discarded when the
run/job ends. There is no retention job to run: transcripts/topics/signals are the
permanent artifacts (database in the web app, `--output` JSON caches in the CLI).

### 2.7 Frontend development (Vite)

```bash
cd frontend && npm run dev     # Vite on :5173, proxy /api → :8000 (run `primocut serve` alongside)
```

### 2.8 Tests

```bash
source .venv/bin/activate
pytest -q                                     # everything (no GPU/network: fakes + inline execution)

# Per module:
pytest tests/test_doctor.py -v                # diagnostics
pytest tests/test_models.py -v                # models + youtube_url
pytest tests/test_chat_signals.py -v          # z-score / peaks
pytest tests/test_windowing.py -v             # overlapping windows
pytest tests/test_analysis_selection.py -v    # index selection (never invents ts)
pytest tests/test_report_markdown.py -v       # report
pytest tests/test_transcription_engine.py -v  # transcription interface
pytest tests/test_downloaders.py -v           # mocked yt-dlp/chat
pytest tests/test_pipeline.py -v              # end-to-end with fakes
```

The suite also covers (Phase 1): migrations/DAO, job state transitions, **idempotent
re-enqueue (cache respected)** and API endpoints.

---

## 3. Architecture

```
[Svelte Web UI] ──/api──> [FastAPI] ──enqueue──> [RQ + Redis] ──> [Worker]
                              │                                      │
                              └──────────────── [SQLite] <───────────┘
                     (videos, jobs, transcripts, chat_signals, topics)
[Temp dir] ← audio files (per job, discarded when the job ends)
```

- **1 RQ job per video** runs the stages sequentially
  (`worker/jobs.py:process_video`), reusing `run_stages` (same logic/resilience as the CLI)
  with `DbStore` (database cache) and `DbReporter` (live status/progress). The CLI `run` uses
  `FileStore` (JSON cache) + Markdown report, identical behavior.
- **Pay-once cache** (see [Resilience §4.2](#42-cache--resume-pay-once)): re-enqueueing skips
  stages whose artifact already exists (transcript/topics) — nothing already completed/paid
  for is reprocessed.
- **UI progress = polling (~2 s)** — chosen for simplicity/robustness (single-user); status
  and progress already live in the database.
- **GPU concurrency:** the transcription stage acquires a **distributed lock in Redis**
  (`primocut:gpu`), guaranteeing **1 transcription at a time** regardless of how many
  `primocut worker` processes run. The lock has a `timeout` (auto-releases if a worker dies):
  `PRIMOCUT_GPU_LOCK_TIMEOUT` (default 2 h).
- **Degradation preserved** and reflected in the UI: a degradable failure → report with a
  "⚠️ Degraded run" section; an essential failure (audio/transcription) →
  `Video.error`/`Job.failed`.

### Database (SQLite)

Tables: `videos, jobs, transcripts, chat_signals, topics, schema_migrations`.
Simple migrations: base schema via `create_all` (v1) + `db/migrations/NNN_*.sql` deltas.
The runner tolerates a duplicate-column error on `ADD COLUMN`, so column-adding deltas are
no-ops on fresh databases (where `create_all` already added the column from the model).

- `Video.status`: `queued → downloading → transcribing → analyzing → done | error`;
  **`stopped`** when interrupted (via `stop`). `reenqueue` reuses the cache; `DELETE` removes
  everything and requires resubmitting the URL to start from scratch.
- Audio = file on disk (deletable). Transcript/topics/signals = **permanent** (database).
- `Job.stage_history`: ordered `[{stage, at}]` transitions (JSON), one per stage change,
  appended by `DbReporter`; a terminal `__end__` marker is recorded on failure/stop so the
  last stage is bounded. The UI derives each stage's **duration** from the gap between
  consecutive `at` timestamps (the running stage ticks up to *now*). Legacy jobs have an empty
  list and simply show no durations. See the "Pipeline stages" panel below.

### API (summary)

| Method | Route | Action |
|---|---|---|
| POST | `/api/videos` `{url}` | validates, creates Video(queued) + Job, enqueues |
| GET | `/api/videos` | list with status/progress |
| GET | `/api/videos/{id}` | detail (video + job) |
| GET | `/api/videos/{id}/report` | report JSON (topics/chat/transcript, 🔥, links) |
| POST | `/api/videos/{id}/topics` | generate macro topics with the LLM (opt-in; clears + re-runs; enqueues a topics-only job) |
| POST | `/api/videos/{id}/reenqueue` | reprocess (idempotent; resets status→queued) |
| POST | `/api/videos/{id}/stop` | interrupts processing (cancels the job/worker; status→stopped) |
| DELETE | `/api/videos/{id}` | deletes EVERYTHING about the video (database + files); refuses (409) if active — stop it first |

### Golden rule — timestamps are never invented

The LLM receives **indexed** segments (`[i] M:SS text`) and returns only
`start_index`/`end_index` (forced tool use); the code resolves the indices → seconds from the
transcript and **clamps** out-of-range indices. It is *impossible* for the LLM to invent a
time. This holds in both the CLI and the web app.

### Code pointers

- Pipeline / orchestration: `src/primocut/pipeline.py`
- Worker / jobs / queue: `src/primocut/worker/`
- Analysis (tolerant parsing, providers, tool use): `src/primocut/analysis/{parse,llm,topics,windowing}.py`
- Transcription (CUDA preload, engine): `src/primocut/transcription/faster_whisper_engine.py`
- Download (URL, yt-dlp, ffmpeg): `src/primocut/download/audio.py`
- Chat (optional): `src/primocut/chat/{downloader,signals}.py`
- Models (resolve_span, clamp): `src/primocut/models.py`
- Database (ORM, DAO, migrations): `src/primocut/db/`
- Diagnostics: `src/primocut/doctor.py`
- Cross-cutting resilience (retry/backoff/classification): `src/primocut/resilience.py`

---

## 4. Resilience

Full catalog of recovery/degradation behaviors. Every item below is **implemented in the code
and covered by tests**.

### Principles

- **Degrade, don't abort.** Whenever possible, produce the best report from what succeeded,
  flagging the unavailable sections. Only abort when it is **impossible to continue**.
- **Essential vs. degradable.** Audio and transcription are essential (without them there is
  nothing to analyze); metadata, chat and analysis are degradable.
- **Pay-once cache.** Expensive stages are persisted and reused; no work already completed
  (and paid for) is redone.
- **Never invent timestamps.** Analysis robustness never relaxes the rule: the LLM selects
  segment indices; the code resolves them into seconds and clamps invalid indices.

### 4.1 Fallbacks per pipeline stage

| Stage | Trigger | Fallback behavior |
|---|---|---|
| **Metadata** | `yt-dlp -J` fails on a transient error (network/rate limit) | Retry with exponential backoff |
| **Metadata** | `yt-dlp -J` fails permanently | **Degrades**: builds a minimal `Video` (id extracted from the URL, title/channel "(unknown)", duration 0); adds a warning; duration is filled in later from `transcript.duration` |
| **Audio download** | Transient yt-dlp error | Retry with backoff |
| **Audio download** | YouTube extractor broke for a client | Tries `--extractor-args youtube:player_client=<c>` over a configurable list (e.g. `android,web`) |
| **Audio download** | Final failure (essential) | **Aborts** with a clear message (without audio there is no transcription) |
| **Transcription** | GPU/VRAM error (does it fit in 8 GB?) | Loop degrading the `compute_type`: `float16 → int8_float16 → int8`; records a warning when it degrades |
| **Transcription** | No `compute_type` fit (essential) | **Aborts** with guidance (no CPU fallback, by decision) |
| **Transcription** | `libcublas`/`libcudnn` not on `LD_LIBRARY_PATH` | Preloads the CUDA libs from the NVIDIA pip packages (`nvidia-cublas-cu12`/`nvidia-cudnn-cu12`); if absent, lets ctranslate2 try the system PATH |
| **Chat** | `chat-downloader` raises / no replay | **yt-dlp `--live-chat` fallback** (parses the message offsets) |
| **Chat** | Both fail / replay does not exist | Returns `None`; the pipeline **continues without chat** (optional signal) and records a warning |
| **Analysis (topics)** | Transient provider error per window (429/5xx/timeout) | Retry with backoff on the same model (without switching model/provider) |
| **Analysis (topics)** | Permanent failure on a window (credit, auth, no tool use) | **Ends the stage keeping whatever was already collected** (partial); the pipeline degrades the report + warning |
| **Analysis (topics)** | LLM response off-format | Tolerant parsing: normalizes an indexed dict, **ignores malformed items with a warning** (never tears down the stage) |
| **Analysis (topics)** | LLM returns out-of-range indices | `resolve_span` **clamps** to the real limits and sorts them if reversed |
| **Report** | Some section has no data | Renders "No topic identified" and/or "No chat replay"; never fails |
| **Report** | The run had degradations | A **"⚠️ Degraded run"** section right after the metadata, listing what failed/was skipped |

### 4.2 Cache / resume (pay-once)

Each artifact in the `--output` dir (CLI/`FileStore`) — and the equivalent in the database
(web app/`DbStore`) — is reused across re-runs:

| Artifact | Stage it avoids repeating |
|---|---|
| `<id>.transcript.json` / DB row | Transcription (the most expensive GPU stage) |
| `<id>.topics.json` / DB row | Macro analysis (LLM, paid) |

- The audio is **not** a cache artifact: it lives in a throwaway temp dir, so a re-run
  re-downloads it — but only when the transcript is missing (a present transcript skips the
  audio+transcription stages entirely).
- The **partial** result of a stage (when ended mid-way by a permanent failure) feeds the
  current report, but is **not** cached — so a re-run tries again. Because the windows are few
  (~2 in a 33-min live), the retry is cheap.
- To force a recompute, delete the corresponding JSON in the `--output` dir (CLI) or
  re-enqueue/regenerate topics (web app).

### 4.3 Cross-cutting robustness guards

| Guard | What it does |
|---|---|
| **URL validation** (`validate_youtube_url`) | Rejects out-of-format links (non-YouTube host, invalid ID, escape backslashes) with a clear message and **does not trigger** the pipeline; returns the canonical `watch?v=<ID>` URL |
| **ID extraction** (`extract_video_id`) | Accepts `watch?v=`, `youtu.be/`, `/live/`, `/embed/`, `/shorts/` and falls back to the last path segment |
| **yt-dlp via module** | Invokes `python -m yt_dlp` through the current interpreter → works even without `.venv/bin` on `PATH` |
| **doctor: yt-dlp** | Considers it OK if the `yt_dlp` **module** is installed (does not require the binary on PATH) |
| **doctor: API key** | Validates the key of the **selected** provider (`llm_provider`) |
| **doctor: provider SDK** | Warns if the provider SDK (openai/anthropic) is not installed |
| **OpenAI SDK import** | If the `openai` package is missing (used by OpenAI/OpenRouter), a clear error stating the install command |
| **No tool support** (`ToolCallError`) | If the model does not return the forced function/tool call, a clear error (probably no tool-use support) + a hint about the `primocut models` command |
| **Chat signals** | `z-score` with standard deviation 0 → no peaks (no division by zero); empty message list → empty signal |
| **`primocut models`** | Lists OpenRouter models that support tools (including **free** ones) to pick a compatible one without spending |

### 4.4 LLM provider selection

The analysis is provider-agnostic (`SelectionLLM` interface). Automatic selection via
`PRIMOCUT_LLM_PROVIDER` (default `anthropic`, `claude-sonnet-5`) — full table in
[How to run §2.4](#24-cli-run-and-models).

> **There is no automatic provider/model switch on failure** (decision: LLM = "retry only").
> Switching provider/model is always explicit, via `.env` or the `--provider`/`--model` flags.

### 4.5 Matrix: essential vs. degradable

| Stage | If it fails for good | Does the report come out? |
|---|---|---|
| Metadata | Degrades (minimal Video) | ✅ Yes, with a warning |
| Audio download | Aborts | ❌ No (essential) |
| Transcription | Aborts | ❌ No (essential) |
| Chat | Continues without chat | ✅ Yes, with a warning |
| Analysis (topics) | Uses the partial (or empty) result | ✅ Yes, with a warning |
| Report | — | ✅ Always generated |

### 4.6 Related environment variables

| Variable | Effect |
|---|---|
| `PRIMOCUT_LLM_PROVIDER` | `anthropic` \| `openai` \| `openrouter` |
| `PRIMOCUT_ANTHROPIC_MODEL` / `PRIMOCUT_OPENAI_MODEL` / `PRIMOCUT_OPENROUTER_MODEL` | Model per provider |
| `PRIMOCUT_WHISPER_COMPUTE_TYPE` | Force the `compute_type` (e.g. `int8_float16` to fit in VRAM) |
| `PRIMOCUT_WHISPER_DEVICE` | `cuda` (default) \| `cpu` |
| `PRIMOCUT_WHISPER_COMPUTE_FALLBACKS` | GPU `compute_type` degradation sequence |
| `PRIMOCUT_CHAT_ZSCORE_THRESHOLD` | Chat peak detection threshold |
| `PRIMOCUT_WINDOW_TARGET_SECONDS` / `PRIMOCUT_WINDOW_OVERLAP_SECONDS` | Analysis windowing |
| `PRIMOCUT_RETRY_ATTEMPTS` / `PRIMOCUT_RETRY_BASE_DELAY` / `PRIMOCUT_RETRY_MAX_DELAY` | Retry (backoff) policy for the stages |
| `PRIMOCUT_YTDLP_PLAYER_CLIENTS` | List of `player_client` values to try on download |
| `PRIMOCUT_GPU_LOCK_TIMEOUT` | GPU lock timeout in Redis (auto-releases; default 2 h) |

---

## 5. Roadmap

Aligned with the product roadmap ([`PRIMOCUT.md`](./PRIMOCUT.md) §Roadmap).

- **Phase 0 — CLI spike ✅ (done).** Command-line script: URL → audio → transcription → chat
  → topics in Markdown. Validated the GPU on WSL2, the quality of the macro-topic segmentation
  and chat peak detection. Delivered the modular architecture (already designed for Phase 1)
  and the test suite.
- **Phase 1 — Web MVP ✅ (done / current base).** Queue (RQ+Redis) + database (SQLite) + API
  (FastAPI) + navigable UI (Svelte) + navigable report +
  embedded player with seek + opt-in topic generation.
- **Phase 2 — Competitor channel modeling (next).** Given a **user-provided** set of competitor
  channels, continuously collect **public** performance data and reveal what is working in the
  niche — outlier videos, packaging patterns (title/thumbnail), posting strategy and winning
  topics — to guide PRIMOCUT's editorial decisions. Channels are supplied by the user (no
  niche-wide channel discovery). **Out of scope:** anything that depends on private YouTube
  Analytics (CTR, retention, impressions — unavailable for third-party channels); clip
  generation/WhisperX (now Phase 3). See the [2026-07-14 decision](#2026-07-14--phase-2-redefined-competitor-channel-modeling-cuttingpublishing-shift-to-34).
- **Phase 3 — Assisted cutting.** Automatic clip generation with FFmpeg; evaluate
  **WhisperX** (word-level timestamps + diarization). The `TranscriptionEngine` interface
  exists precisely so WhisperX can be plugged in without touching the rest of the system.
- **Phase 4 — Publishing.** AI titles/descriptions, vertical format, upload via the YouTube
  API, multi-user.

---

## 6. Decision log

The most important section. **Never overwrite or delete an entry**: a reverted decision gets a
**new** entry pointing to the reversal. Dates in ISO (`YYYY-MM-DD`).

### 2026-07-14 · Phase 2 redefined: competitor channel modeling (cutting/publishing shift to 3/4)
**Type:** product/business.
**Decision:** Phase 2 is redefined from "Assisted cutting" to **Competitor channel modeling**.
Given a **user-provided** set of competitor channels, PRIMOCUT continuously collects **public**
performance data and surfaces what is working in the niche — outlier videos, packaging patterns
(title/thumbnail), posting cadence and winning topics — to inform editorial decisions. The former
Phase 2 (assisted cutting: FFmpeg clip generation + WhisperX evaluation) moves to **Phase 3**, and
publishing moves to **Phase 4**.
**Scope:** channels are supplied by the user (no niche-wide channel discovery); data is **public
only**.
**Out of scope — by decision, not just "later":** anything that depends on **private YouTube
Analytics** (CTR, retention, impressions), which is structurally **unavailable for third-party
channels**; global channel discovery; and clip generation/WhisperX (now Phase 3).
**Reason:** before investing in cutting/rendering, understand the niche's demand and packaging from
real competitor performance, so the editorial choices PRIMOCUT already surfaces (topics / clip
candidates) are grounded in what actually performs. Public data is enough for the outlier and
packaging signal; the private-analytics metrics simply cannot be obtained for other channels, so
the phase is scoped around what is collectable.
**Supersedes:** the prior roadmap ordering (Phase 2 = assisted cutting, Phase 3 = publishing); both
shift down one and keep their content.

### 2026-07-13 · Audio is ephemeral; `data/` holds only the DB (reverses 2026-07-12 retention)
**Type:** technical.
**Decision:** the normalized audio is written to a throwaway temp dir (`tempfile`) — both in
the CLI (`run_pipeline`) and in the worker (`process_video`) via the new `run_stages(audio_dir=…)`
parameter — and discarded when the run/job ends. The CLI now takes `-o/--output` as an **output
directory** (default `.`) where it exports `report.md` + the transcript/topics JSON; it no longer
uses `data/` at all. The web app keeps only the SQLite DB in `data/` (`db_path`). Removed the now-
dead retention code (`worker/cleanup.py`, the `cleanup` CLI command, `schedule_daily_cleanup`,
`purge_video_files`, and the `audio_retention_days` setting); `delete_video` only deletes DB rows.
**Reason:** audio is a large, recomputable intermediate; keeping it on disk with a retention job
added moving parts for no benefit now that the permanent artifacts (transcript/topics/chat) live
in the DB (web) or the `--output` JSON caches (CLI). A temp dir self-cleans and removes the
retention/purge machinery entirely.
**Supersedes:** the 2026-07-12 "Retention: audio temporary, transcript permanent" entry below.

### 2026-07-13 · Real per-stage timing in the pipeline stages panel
**Type:** product + technical.
**Decision:** show each pipeline stage's **duration** in the "Pipeline stages" panel. To feed
real durations, `Job` gains a `stage_history` JSON column (migration `003`); `DbReporter`
appends a `{stage, at}` transition on every stage change, and failure/stop append an `__end__`
marker. Durations are derived in the frontend (`lib/steps.js:stepDurations`) from the gaps
between timestamps, so the running stage ticks up to *now* via the existing 2 s polling. The
existing **list** layout (compact dot row + expandable per-stage details) is kept; the duration
sits next to each stage's status badge.
**Reason:** the panel didn't convey where time is spent; the durations make the GPU
transcription stage's cost visible.
**Rejected alternatives:** a horizontal "CI pipeline" of connected stage cards (GitHub Actions
style) — built first, then **reverted**: it forced horizontal scrolling and read poorly at this
width; only the durations were kept. Deriving a single total from `created_at → updated_at` (no
per-stage breakdown); a separate `job_steps` table (heavier than a JSON column, which mirrors
the existing `warnings` column). **Note:** legacy jobs (empty history) render without
durations — timing only appears for runs after this change.

### 2026-07-12 · Plain Python for the MVP
**Type:** technical.
**Decision:** implement the MVP in "plain" Python (typer + pydantic + FastAPI), with no heavy
framework or premature abstractions, but with clean module boundaries.
**Reason:** local single-user spike; the priority is to validate the pipeline (GPU,
segmentation quality, chat) fast, keeping the code ready to become Phase 1 without rewriting
the logic.

### 2026-07-12 · venv + pip instead of uv
**Type:** technical.
**Decision:** manage dependencies with `venv` + `pip` + `requirements.txt`; `pyproject.toml`
(setuptools) only registers the `primocut` entry point.
**Reason:** native tooling, no new tools to introduce right now.
**Rejected alternatives:** `uv`/`poetry` — **uv deferred** until explicitly authorized, not
definitively rejected.
**Environment note:** on WSL2 the `python3.13-venv` package is missing; the venv was created
`--without-pip` and installs use the system pip pointed at the venv's interpreter (see
[How to run §2.1](#21-environment-setup-native-python-venv--pip)).

### 2026-07-12 · Golden rule: forced tool use with segment indices
**Type:** technical.
**Decision:** the LLM receives indexed segments and returns only `start_index`/`end_index`
via **forced tool use**; the code resolves the indices → seconds and clamps invalid ones.
**Reason:** makes it **impossible** for the LLM to invent a timestamp — the product's golden
rule (timestamps are always real, `&t=SECONDS` links are always correct).
**Rejected alternatives:** asking the LLM to generate time numbers directly (fragile,
hallucinates timestamps).

### 2026-07-12 · faster-whisper `large-v3-turbo` locally on the GPU
**Type:** technical.
**Decision:** transcribe with faster-whisper `large-v3-turbo`, `device=cuda`, behind the
`TranscriptionEngine` interface. No CPU fallback.
**Reason:** PT-BR quality + speed on the RTX 3070 Ti (8 GB); run 100% locally with no
transcription API cost. The interface lets WhisperX replace it in Phase 3 without touching the
rest.
**Rejected alternatives:** distil models (English-only, unusable for PT-BR); **CPU fallback**
(too slow for 3-4 h lives — better to abort with guidance). Instead of CPU, the `compute_type`
is degraded on the GPU itself (`float16 → int8_float16 → int8`).

### 2026-07-12 · Chat as an optional signal (promoted to the MVP)
**Type:** technical/business.
**Decision:** treat the chat replay as an **optional signal** — peak detection by z-score
(msgs/min) and reaction markers (laughs "KKKK", emotes); a missing replay does **not** block
the pipeline. The feature, born in the Phase 0 spike, was **kept/promoted to the MVP**
(series + peaks in the report, 🔥 marker on topics that coincide with peaks).
**Reason:** chat peaks are good clip candidates and add real value; but not every live
preserves the replay, so it cannot be a hard dependency.

### 2026-07-12 · Multi-provider LLM with Anthropic default
**Type:** technical.
**Decision:** provider-agnostic analysis (`SelectionLLM` interface) supporting Anthropic
(default, `claude-sonnet-5`), OpenAI (`gpt-4o`) and OpenRouter (`openai/gpt-4o-mini`,
`provider/model` slugs). Selection via `PRIMOCUT_LLM_PROVIDER` or flags.
**Reason:** cost/quality flexibility and the ability to validate with free models via
OpenRouter (`primocut models --free`) without spending.

### 2026-07-12 · No automatic provider switch (LLM = "retry only")
**Type:** technical.
**Decision:** on an LLM failure there is only **retry with backoff on the same
model/provider**; never an automatic switch of provider or model. Switching is always explicit
(`.env` or flags).
**Reason:** automatic switching hides cost/behavior and hurts predictability; permanent
failures (credit/auth/no tool use) should be visible, not masked.
**Rejected alternatives:** a provider fallback chain — left out by decision; remains a
technical candidate for future phases.

### 2026-07-12 · SQLite as the database
**Type:** technical.
**Decision:** persist videos, jobs, transcripts, chat signals and topics in SQLite, with
simple migrations (`create_all` v1 + `NNN_*.sql` deltas).
**Reason:** 100% local single-user application; SQLite is zero-config, sufficient and portable.
**Rejected alternatives:** Postgres/a database server (unnecessary overhead for this scope).

### 2026-07-12 · RQ + Redis instead of Celery
**Type:** technical.
**Decision:** job queue with RQ over Redis; 1 job per video running the stages sequentially,
reusing `run_stages`.
**Reason:** RQ is simple and sufficient for single-user; Redis already comes in as a
dependency for the GPU lock.
**Rejected alternatives:** Celery (broker/config/complexity beyond what is needed here).

### 2026-07-12 · Retention: audio temporary, transcript permanent
**Type:** business.
**Decision:** audio is a file on disk, deleted by a `cleanup` after N days (default 7,
configurable); transcripts, topics and chat signals are **permanent** in the database.
**Reason:** audio is large and recomputable (re-download); the expensive/derived artifacts
(transcript, analysis) must survive. Cleanup **never** touches transcripts/topics.

### 2026-07-12 · Distributed GPU lock in Redis
**Type:** technical.
**Decision:** the transcription stage acquires a distributed lock in Redis (`primocut:gpu`)
with a timeout (`PRIMOCUT_GPU_LOCK_TIMEOUT`, default 2 h; auto-releases if a worker dies),
guaranteeing **1 transcription at a time**.
**Reason:** the GPU (8 GB) fits only one transcription; the lock makes it **safe to run N
workers** — the light stages of different videos overlap, but only one enters the GPU.

### 2026-07-12 · Polling instead of SSE
**Type:** technical.
**Decision:** the UI follows status/progress via **polling** (~2 s) over the REST endpoints.
**Reason:** status and progress already live in the database; polling is simpler and more
robust for a single-user app than keeping connections open.
**Rejected alternatives:** SSE/WebSocket (complexity with no gain in the current scope).

### 2026-07-12 · Svelte frontend
**Type:** technical.
**Decision:** UI as a Svelte SPA, compiled with Vite and served as static files by FastAPI.
**Reason:** light bundle, simple DX; a single process (`serve`) delivers API + UI.

### 2026-07-13 · Project language = English; single-pass analysis
**Type:** technical.
**Decision:** all code, docstrings, identifiers, logs, error messages and UI strings — plus
**all documentation** (product and development) — are written in English; the analysis now
runs in a **single pass** (macro topics).
**Reason:** project consistency and a simpler analysis flow (see the "cut moments" removal
decision below).

### 2026-07-13 · Removal of "cut moments"; unlimited macro topics
**Type:** business/technical.
**Decision:** remove the second analysis pass (fine-grained "cut moments" detection) and the
10-20 topic cap; the analysis now has a **single macro-topics pass with no numeric cap**. The
`analysis/moments.py` module and the `CutMoment` model no longer exist.
**Reason:** the fine-grained pass did not pay off against the macro topics; it simplifies the
pipeline, cuts LLM cost and aligns with the real value (a navigable index of subjects).
**Rejected alternatives / history:** Phase 0 originally explored two independent passes (macro
+ fine-grained moments) precisely to **compare segmentation quality** — comparison done, the
fine pass was discontinued.

### 2026-07-13 · Embedded YouTube player with seek
**Type:** technical.
**Decision:** the web report embeds the official YouTube player; each topic's **Watch** button
seeks the player to the corresponding timestamp (and the interaction scrolls the page to the
top). Topics live inside a collapsible card (expand/collapse all).
**Reason:** reviewing a passage without leaving the report speeds up the cut decision.

### 2026-07-13 · Opt-in macro topic generation
**Type:** business.
**Decision:** macro topics are **not** generated automatically; they are on demand via the
**"Generate topics with AI"** button (web) or `--create-topics` (CLI). The
`POST /api/videos/{id}/topics` endpoint clears and re-runs, enqueuing a topics-only job.
**Reason:** the analysis costs an LLM call (paid); the transcript and chat, which cost no API,
always come out — the user pays for the analysis only when they want that specific video.

---

## 7. Update convention

Rules to keep this document alive going forward:

- **Every relevant change adds an entry** to the [decision log](#6-decision-log). **Never**
  overwrite or delete an old entry: a reverted decision gets a **new**, dated entry that
  explicitly points to the reversal of the previous one.
- **Always-current sections:** [Current state](#1-current-state), [How to run](#2-how-to-run)
  and [Architecture](#3-architecture) reflect the **actual state of the code** at every change
  — never plans or intentions. Content that has become history leaves those sections and
  becomes a decision-log entry.
- **Contradictions** between this document and the code are always resolved in favor of the
  code.
- **Language:** everything that goes into the project — code, comments, identifiers, strings
  and **all documentation** (`PRIMOCUT.md`, this file, README) — is written in **English**.
  Portuguese is used **only** to talk to the maintainer, never in a project artifact.
