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
  including free ones), `serve`, `worker`, `scheduler`, and the Phase 2 sub-apps `projects`
  (`add`/`list`/`remove`), `competitors` (`add`/`detach`/`list`/`snapshot`) and `metrics`
  (`refresh`/`show`).
- **Local web app** (`primocut serve` + `primocut worker`): a Svelte SPA served by FastAPI,
  an RQ+Redis queue and a SQLite database. You submit a URL, follow the live status
  (polling) and open the report in the browser.

**Phase 2 foundation (competitor channel modeling — in progress).** The data foundation is
in place: **projects** group **global competitor channels** (M:N, deduplicated by
`channel_id`); registering a channel resolves its URL/@handle/ID via the **YouTube Data API
v3** and, when the channel is new to the system, enqueues an async **backfill** of its last
120 uploads (metadata + `is_short` + initial `thumbnail_hash`) into `competitor_videos`;
re-registering a known channel only links it (no re-collection). Detaching a channel — or
deleting a project — always preserves channel/video data and deactivates a channel that has
become orphaned from every project. The YouTube client accounts quota per operation
(`videos.list` batches up to 50 IDs = 1 unit) and `doctor` validates the key with a real,
cheap call.

**Continuous collection** is also in place: an rq-scheduler process (`primocut scheduler`) runs
two cadences of the same `snapshot_cycle` — a **daily** pass over all videos of active channels
and a **6-hourly** pass over videos younger than 7 days. Each cycle writes `channel_snapshots`
(subscribers, total views, video count) and `video_snapshots` (views, likes, comments; indexed
by `(video_id, captured_at)`), detects **new uploads** (top of the uploads playlist → added to
the 6h cycle) and **packaging changes** (title by string; thumbnail by **byte hash** — the URL
is not trusted — recorded in `packaging_changes`, with `vph_before/after` left NULL for the
metrics step). Collection is per-channel isolated (a failing channel is logged and skipped, the
cycle never aborts), orphan channels are excluded, and quota is logged per cycle.
`primocut competitors snapshot` runs a cycle inline for manual testing.

**Metrics engine** is now in place — deterministic, no LLM, and **computed on read** over the
snapshots (no score is ever stored). It exposes three read-only dashboards:
`GET /api/projects/{pid}/dashboard`, `GET /api/channels/{cid}/dashboard` and
`GET /api/channels/{cid}/videos/{vid}`. It computes: the **outlier score**
(`views ÷ channel median baseline`, with **separate Shorts/long-form baselines** over a
rolling *min(50 videos, 180 days)* window, **provisional** scores under 7 days, an
**insufficient_data** guard under 10 same-format peers, and configurable **3x/6x/10x**
brackets); **VPH strictly from snapshot deltas** (current = last-24 h delta; never
`total_views ÷ age`); **resurgence** alerts (old video whose current VPH beats *k×* its recent
average); **packaging impact** (mean VPH 7 days before vs after a change, filled into
`packaging_changes.vph_before/after` once the window elapses); **engagement** (likes/comments
per view) and **cadence** (uploads/week, weekday/hour distribution, duration per format); and
**per-project conditional distributions** of **syntactic title attributes** (regex/heuristic,
persisted in `video_attributes`) among outliers vs the population, gated by a
**minimum-occurrence guard**. The two persisted artifacts (syntactic attributes + packaging
impact) are written by the snapshot cycle and by `primocut metrics refresh`; the dashboards
stay pure reads. **Not yet built:** AI enrichment (thumbnail/title LLM attributes, comment
synthesis) and the Phase 2 UI.

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
the YouTube Data API key (a real 1-unit call), Redis and the database**, and prints the fix
command for each pending item. Exits with code `1` while there are pending items. The YouTube
check fails when `PRIMOCUT_YOUTUBE_API_KEY` is unset (it is a Phase 2 requirement, like the
LLM provider key).

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

### 2.8 Competitor projects (Phase 2 foundation)

Needs `PRIMOCUT_YOUTUBE_API_KEY` (YouTube Data API v3; free tier 10,000 units/day). A backfill
runs asynchronously on the existing queue, so `primocut worker` must be running (and Redis up)
for a new channel's uploads to be collected.

```bash
# Projects (one per niche/clip channel you run)
primocut projects add "Cortes BR" -d "my niche"
primocut projects list
primocut projects remove <PROJECT_ID>          # detaches channels; never deletes channel data

# Competitor channels within a project (URL, @handle or channel ID)
primocut competitors add "https://www.youtube.com/@SomeChannel" --project <PROJECT_ID>
primocut competitors list --project <PROJECT_ID>     # shows backfill state (done/total)
primocut competitors detach <CHANNEL_ID> --project <PROJECT_ID>
```

A channel that is new to the system triggers a backfill of its last 120 uploads; a channel
already known is only linked (global dedup, no re-collection). Detaching a channel that ends
up orphaned from every project deactivates it (`active=false`) but keeps all its data.

**Continuous collection** (snapshots). Start the scheduler alongside the worker; it enqueues the
snapshot cycles onto the same queue (Redis must be up):

```bash
primocut scheduler     # daily pass (all videos) + 6h pass (videos < 7 days), on cron
```

The daily pass (`0 3 * * *`) snapshots every video of every active channel; the 6h pass
(`0 */6 * * *`) snapshots only videos younger than `PRIMOCUT_YOUTUBE_RECENT_DAYS` (7). Both
also detect new uploads and packaging changes. Cadence and windows are configurable
(`PRIMOCUT_SNAPSHOT_DAILY_CRON`, `PRIMOCUT_SNAPSHOT_RECENT_CRON`, `PRIMOCUT_YOUTUBE_RECENT_DAYS`).
Run one cycle by hand (no scheduler/worker needed) for testing:

```bash
primocut competitors snapshot                      # all active channels, all videos
primocut competitors snapshot --scope recent       # only videos < 7 days old
primocut competitors snapshot --channel <UC...>     # a single channel
```

**Metrics** (computed on read over the snapshots — no worker needed to *query* them). The
scheduler already persists the two derived artifacts each cycle; run the persistence step by
hand, or inspect a dashboard as JSON:

```bash
primocut metrics refresh                    # persist syntactic attributes + packaging impact (idempotent)
primocut metrics show --project <PID>       # niche outlier ranking, patterns, packaging, resurgences
primocut metrics show --channel <UC...>      # channel baselines, outliers, engagement, cadence
primocut metrics show --channel <UC...> --video <VID>   # snapshot series + VPH + attributes
```

The same three views are exposed over HTTP: `GET /api/projects/{pid}/dashboard`,
`GET /api/channels/{cid}/dashboard`, `GET /api/channels/{cid}/videos/{vid}`. Baseline window,
brackets, provisional/resurgence/impact/pattern thresholds are all configurable
(`PRIMOCUT_OUTLIER_*`, `PRIMOCUT_VPH_*`, `PRIMOCUT_RESURGENCE_*`, `PRIMOCUT_PACKAGING_IMPACT_*`,
`PRIMOCUT_PATTERN_*` — see `.env.example`).

### 2.9 Tests

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
re-enqueue (cache respected)** and API endpoints. Phase 2 foundation: YouTube client with
**quota accounting** (fully faked, no network), channel resolution, backfill of 120,
**cross-project dedup**, detach/delete **data preservation**, **orphan deactivation**, and
the projects/competitors API + CLI. Phase 2 continuous collection: **frequency selection**
(daily vs 6h), snapshot recording, **new-video detection**, **title change**, **thumbnail
change by byte hash under an unchanged URL**, **per-channel isolation**, orphan exclusion,
per-cycle quota, and idempotent scheduler registration (all faked, no network). Phase 2 metrics
engine (synthetic fixtures, no network): **median baseline robust to a viral in the window**,
**separate Shorts/long-form baselines**, **provisional vs definitive**, **insufficient_data**,
**VPH from deltas** (and `None` — never fabricated — with a single snapshot), **resurgence**,
**packaging before/after** (persisted only once the window elapses), **minimum-occurrence guard
suppressing a small-sample pattern**, and a **channel shared by two projects yielding identical
metrics with scoped reports**.

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

Tables: `videos, jobs, transcripts, chat_signals, topics, schema_migrations`, the Phase 2
foundation `projects, competitor_channels, project_channels, competitor_videos`, the
collection time series `channel_snapshots, video_snapshots, packaging_changes`, and the metrics
artifact `video_attributes` (persisted syntactic title features, unique per `(video_id,
source)`). Outlier scores, VPH, resurgence and pattern distributions are computed on read over
the snapshots — never stored; the only persisted derived figures are `video_attributes` and
`packaging_changes.vph_before/after`.
Simple migrations: base schema via `create_all` (v1) + `db/migrations/NNN_*.sql` deltas.
The runner tolerates a duplicate-column error on `ADD COLUMN`, so column-adding deltas are
no-ops on fresh databases (where `create_all` already added the column from the model).

**New tables are created by `create_all`, not by a migration.** `create_all` issues
`CREATE TABLE` only for tables that do not yet exist — on both a fresh and an existing
database — so it is the single mechanism for adding a whole table (exactly how the v1 base was
made). `NNN_*.sql` migrations are reserved for `ALTER`/`DROP` on existing tables, which
`create_all` cannot perform. The ORM is the single source of truth for table shape; migrations
carry only the imperative deltas.

- **Multi-project semantics:** `competitor_channels` is global (deduplicated by `channel_id`),
  `competitor_videos` is deduplicated by `video_id`, and `project_channels` is the M:N link.
  The `ondelete=CASCADE` lives **only** on `project_channels`, so deleting a project detaches
  its channels without ever touching channel/video data; a channel orphaned from every project
  is set `active=false` in code (never deleted). A channel's `backfill_state/done/total` is
  polled like the video pipeline; the backfill runs once per channel, globally.

- **Snapshot scheduler:** `primocut scheduler` runs rq-scheduler, which registers two cron jobs
  (idempotent, cancel-by-id) of the same `snapshot_cycle(scope)` function — `scope="all"` daily,
  `scope="recent"` every 6h — and moves due jobs onto the `primocut` queue, where the worker
  executes them. The daily hour is offset from the 6h grid so the two never fire in the same
  minute. Selection: `all` → every video of active channels; `recent` → the same, filtered to
  `published_at >= now - youtube_recent_days`. One `YouTubeClient` per cycle gives a per-cycle
  quota total (logged); channels are processed in isolation. Code: `worker/scheduler.py`,
  `worker/snapshot_jobs.py`.

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

Phase 2 foundation (projects + competitor channels):

| Method | Route | Action |
|---|---|---|
| POST | `/api/projects` `{name, description?}` | create project (409 on duplicate name) |
| GET | `/api/projects` | list projects (+ channel counts) |
| GET | `/api/projects/{pid}` | project detail + its channels (with backfill state) |
| PATCH | `/api/projects/{pid}` `{name?,description?,active?}` | update project |
| DELETE | `/api/projects/{pid}` | delete project; detaches channels, deactivates orphans; **keeps channel data** |
| POST | `/api/projects/{pid}/channels` `{url}` | resolve (1 API unit) + link; new channel → enqueue backfill; known channel → link only (dedup) |
| GET | `/api/projects/{pid}/channels` | list the project's channels + backfill state (polling) |
| DELETE | `/api/projects/{pid}/channels/{cid}` | detach; deactivates if orphaned; **keeps data** |

Phase 2 metrics dashboards (computed on read; **pure reads, no writes**):

| Method | Route | Action |
|---|---|---|
| GET | `/api/projects/{pid}/dashboard` | niche outlier ranking, per-project attribute patterns (guarded), packaging with before/after impact, resurgences |
| GET | `/api/channels/{cid}/dashboard` | per-format baselines, outlier ranking, engagement medians, cadence, resurgences |
| GET | `/api/channels/{cid}/videos/{vid}` | snapshot series, VPH (current + pairwise), outlier score, engagement, resurgence, syntactic attributes, packaging changes |

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
- Database (ORM, DAO, migrations): `src/primocut/db/` (Phase 2 DAO: `db/competitors.py`)
- Diagnostics: `src/primocut/doctor.py`
- Cross-cutting resilience (retry/backoff/classification): `src/primocut/resilience.py`
- YouTube Data API v3 client (quota accounting, resolution, thumbnails): `src/primocut/youtube/`
- Metrics engine (pure core + persistence + dashboards): `src/primocut/metrics/`
  (`core.py` = DB-free methodology; `attributes.py`/`impact.py` = the persisted artifacts;
  `patterns.py` = per-project distributions; `service.py` = dashboard payloads). HTTP routers:
  `api/metrics.py` (channel/video) + the project dashboard in `api/projects.py`.
- Competitor channel registration/backfill (queue + job): `src/primocut/worker/channels.py`,
  `src/primocut/worker/channel_jobs.py`
- Snapshot collection (cycle job + scheduler): `src/primocut/worker/snapshot_jobs.py`,
  `src/primocut/worker/scheduler.py`
- Projects/competitors API router: `src/primocut/api/projects.py`

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
| **Channel backfill** | YouTube API error (quota/auth/network) | **Degrades**: the channel is marked `backfill_state="error"` with the message logged; the job returns instead of crashing the worker (per-channel isolation) |
| **Channel backfill** | A thumbnail cannot be fetched/hashed | Stores an empty `thumbnail_hash` and continues (a missing hash never fails the backfill) |
| **Snapshot cycle** | A channel's API/thumbnail call fails | **Per-channel isolation**: the error is logged and the cycle moves to the next channel; it never aborts the whole cycle nor lets the exception crash the scheduler |
| **Snapshot cycle** | A thumbnail cannot be fetched/hashed | Empty hash; no spurious packaging-change event (a change needs both old and new hashes non-empty) |
| **Snapshot cycle** | Channel deactivated (orphaned) | Excluded from selection — collection stops until the channel is re-linked (`active=true`) |
| **Snapshot cycle** | Metrics-artifact persistence fails (attributes/impact) | Isolated in its own step: logged and skipped, **never affecting the collected snapshots** of the cycle |
| **Metrics dashboards** | Too few videos / a single snapshot | **Degrade, not crash**: an outlier score becomes `insufficient_data`, a VPH becomes `None`, a small-sample pattern is suppressed — the dashboard still renders |

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
| `PRIMOCUT_YOUTUBE_API_KEY` | YouTube Data API v3 key (Phase 2 competitor modeling) |
| `PRIMOCUT_YOUTUBE_SHORT_MAX_SECONDS` | Duration threshold for the `is_short` heuristic (default 60) |
| `PRIMOCUT_YOUTUBE_BACKFILL_LIMIT` | Uploads collected on a new channel's backfill (default 120) |
| `PRIMOCUT_SNAPSHOT_DAILY_CRON` / `PRIMOCUT_SNAPSHOT_RECENT_CRON` | Snapshot cadences (daily / 6h) |
| `PRIMOCUT_OUTLIER_WINDOW_VIDEOS` / `PRIMOCUT_OUTLIER_WINDOW_DAYS` | Baseline rolling window (smaller of the two) |
| `PRIMOCUT_OUTLIER_MIN_SAMPLE` / `PRIMOCUT_OUTLIER_PROVISIONAL_DAYS` / `PRIMOCUT_OUTLIER_BRACKETS` | Insufficient-data guard, provisional cutoff, bracket tiers |
| `PRIMOCUT_VPH_CURRENT_WINDOW_HOURS` | Trailing window for the "current" VPH (default 24) |
| `PRIMOCUT_RESURGENCE_K` / `PRIMOCUT_RESURGENCE_MIN_AGE_DAYS` / `PRIMOCUT_RESURGENCE_BASELINE_DAYS` / `PRIMOCUT_RESURGENCE_MIN_VPH` | Resurgence detection |
| `PRIMOCUT_PACKAGING_IMPACT_WINDOW_DAYS` | Before/after window for packaging impact (default 7) |
| `PRIMOCUT_PATTERN_MIN_OCCURRENCE` / `PRIMOCUT_PATTERN_OUTLIER_BRACKET` | Pattern minimum-occurrence guard + outlier threshold |

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

### 2026-07-14 · Phase 2 metrics engine: deterministic scores computed on read
**Type:** technical (methodology — `PRIMOCUT.md` guideline 10 is non-negotiable).
**Decision:** compute every derived score **on read** over the collected snapshots; store no
score. Key methodology decisions:
- **Median baselines, never the mean.** The outlier score is `latest views ÷ channel median
  baseline`, where the baseline is the median of the same-format videos in a rolling window =
  the smaller of *last 50 videos* and *last 180 days*. The median is robust to a viral inside
  the window (a mean would be wrecked by it). **Separate baselines for Shorts and long-form.**
- **Provisional + insufficient-data guards.** A video younger than 7 days is scored
  **provisional** (it has not accumulated views against a mature baseline); a channel/format
  with fewer than 10 videos yields **insufficient_data** instead of a misleading number.
  Brackets 3x/6x/10x are configurable.
- **VPH strictly from snapshot deltas.** Current VPH = the delta over the last 24 h (latest
  snapshot vs the newest at/before `latest − 24h`). With a single snapshot VPH is `None` — it
  is **never** fabricated from `total_views ÷ age`.
- **Resurgence** = an old video (≥30 d) whose current VPH ≥ *k×* its recent trailing-average
  VPH, with a minimum-absolute-VPH guard against a ~0 baseline.
- **Per-project conditional distributions** of syntactic title attributes among definitive
  outliers vs the population, gated by a **minimum-occurrence guard** (default 15) — below it the
  sample is too small and the pattern is **not reported**.
- **Two persisted artifacts only.** The methodology forbids storing scores, but the data model
  mandates persisting the **syntactic title attributes** (`video_attributes`, content-hash gated
  so they recompute only when the title changes and are shared across projects) and the
  **packaging before/after impact** (`packaging_changes.vph_before/after`, filled only once the
  7-day window elapses). Their persistence is triggered by the **snapshot cycle** and by
  `primocut metrics refresh`; the three dashboard endpoints stay **pure reads**.
- **Shared-channel correctness.** Per-channel/per-video metrics depend only on the channel's own
  snapshots, so a channel in two projects yields identical figures; project dashboards merely
  **scope** which channels/videos are ranked and which population feeds the pattern guard.
- **Pure core.** `metrics/core.py` is DB-free (dataclasses in/out) so the methodology is
  unit-tested directly on synthetic fixtures (median-robust-to-a-viral, format separation,
  provisional/insufficient, VPH-from-deltas, resurgence, packaging before/after).
**Reason:** guideline 10 makes methodological correctness the whole point of this layer;
computing on read keeps the snapshots the single source of truth and lets thresholds change
without recollecting or rescoring history.

### 2026-07-14 · Phase 2 continuous collection: scheduled snapshots + packaging detection
**Type:** technical.
**Decision:** add deterministic, quota-accounted collection on top of the foundation (no
metrics/enrichment/UI yet). Key implementation decisions:
- **Two cron registrations of one `snapshot_cycle(scope)` function.** `scope="all"` runs daily
  (`0 3 * * *`); `scope="recent"` runs every 6h (`0 */6 * * *`, videos younger than
  `youtube_recent_days`). The daily hour is **offset from the 6h grid** so the two never fire in
  the same minute (no duplicate snapshot at a coincident tick). A young video gets 4×(6h) +
  1×(daily) = 5 readings/day (better VPH resolution, the intent); an old video gets 1×/day.
- **Snapshot tables via `create_all`** (`channel_snapshots`, `video_snapshots` with the required
  `(video_id, captured_at)` index, `packaging_changes`), consistent with the foundation's
  no-migration-for-new-tables decision. All rows of a cycle share one `captured_at`/`detected_at`
  so "last snapshot" queries align.
- **Packaging detection compares against the last-known state** stored on `competitor_videos`
  (title string; thumbnail by **byte hash — the URL is not trusted**, so we re-download and hash
  on **every** snapshot, both passes). A change needs both old and new values non-empty (a
  first-seen "" hash is filled silently, no spurious event). `packaging_changes.vph_before/after`
  are written **NULL** — the metrics step (Phase 2 step 3) fills them.
- **Per-channel isolation with one client per cycle.** The cycle loops active channels wrapped in
  try/except (log + continue), so one channel never aborts the cycle nor crashes the scheduler;
  the shared client's `units_used` is the **per-cycle quota total**, logged at the end. Orphan
  channels (`active=false`) are excluded by the selection query; re-linking resumes collection.
- **New-video detection every cycle** from the top of the uploads playlist
  (`youtube_uploads_scan_limit`, 1 unit/channel); new videos are added and enter the 6h cycle.
- **Scheduler as `primocut scheduler`** (registers both crons idempotently by id, then runs the
  rq-scheduler loop); jobs execute in `primocut worker`. `primocut competitors snapshot` runs a
  cycle inline for manual testing. Job ids use **dashes, not colons** (RQ rejects colons).
**Reason:** deliver the collection layer the metrics engine will read from, with correctness
(median-friendly time series, byte-hash packaging) and resilience (per-channel isolation)
locked in first.
**Rejected alternatives:** thumbnail comparison by URL (unreliable — YouTube reuses URLs for
changed images); a single merged pass with a per-video "is it due?" check (two independent crons
are simpler and self-document the cadence); persisting per-cycle quota to a table (premature — no
consumer yet); a separate progress table for cycles (a cycle is transient, unlike the
once-per-channel backfill). **Test note:** the scheduler wrapper is tested against a stub
scheduler (fakeredis incurs a ~10s connect stall per op in this environment); job-id format is
guarded with RQ's own `validate_job_id`.

### 2026-07-14 · Phase 2 foundation: projects, global channels and the YouTube client
**Type:** technical.
**Decision:** implement the Phase 2 data foundation (no snapshots/metrics/enrichment/UI yet):
`projects`, global `competitor_channels`, the `project_channels` M:N link and
`competitor_videos`, plus a quota-accounted YouTube Data API v3 client, an async backfill job
and the projects/competitors API + CLI. Key implementation decisions:
- **New tables come from `create_all`, not a migration.** `create_all` creates any missing
  table on both fresh and existing DBs (as it did for the v1 base); `NNN_*.sql` migrations are
  reserved for `ALTER`/`DROP` that `create_all` cannot do. The ORM is the single source of
  truth for table shape. Writing a redundant `CREATE TABLE` migration would either be a no-op
  (with `IF NOT EXISTS`, since `create_all` always runs first) or crash ("table already
  exists"), and would duplicate the DDL (drift risk). So: **no `005_*.sql`**.
- **Quota accounting is in-memory + logged per operation** (`YouTubeClient.units_used`, one log
  line per call with the cumulative total); the backfill logs the total consumed and the doctor
  reports its probe's units. Batching is exploited: `videos.list` with up to 50 IDs = 1 unit.
  **No DB persistence of quota yet** — that belongs with the scheduled snapshot cycles.
- **`is_short` is a duration heuristic** (`duration_s <= PRIMOCUT_YOUTUBE_SHORT_MAX_SECONDS`,
  default 60): the Data API does not flag Shorts. Refined in a later phase; it feeds the
  separate Shorts baseline.
- **Initial `thumbnail_hash` = SHA-256 of the thumbnail image bytes** (not the URL), computed at
  backfill via an **injectable fetcher** (tests run without network); a fetch/hash failure
  degrades to an empty hash. Byte hashing (vs. URL hashing) lets a later snapshot detect a
  packaging change even when YouTube keeps the same thumbnail URL.
- **Backfill progress lives on the channel row** (`backfill_state/done/total`), polled like the
  video pipeline, because backfill runs once per channel globally (not per project link).
- **Doctor requires `PRIMOCUT_YOUTUBE_API_KEY`** and validates it with a real 1-unit call
  (fails when unset, consistent with the LLM provider key); check count 7 → 8.
**Reason:** get the multi-project invariants (global dedup, orphan deactivation, data
preservation on detach/delete) and the quota-safe collection primitive right first, so the
snapshot/metrics engine builds on a correct foundation.
**Rejected alternatives:** a redundant `005` `CREATE TABLE` migration (drift/no-op, see above);
persisting quota to a table now (premature — no scheduled cycle yet); a separate `channel_jobs`
table for backfill progress (the channel row already models a once-per-channel operation);
`search.list` for resolution (100 units — avoided by mapping every reference to `id`/`forHandle`/
`forUsername` at 1 unit).

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
