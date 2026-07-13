# PRIMOCUT — Phase 1 (MVP): queue + database + API + Web UI

Turns the Phase 0 CLI pipeline into a local web application, **reusing** the pipeline modules
(no rewritten logic). Source of truth: [`PRIMOCUT.md`](./PRIMOCUT.md).
Resilience: [`FALLBACKS.md`](./FALLBACKS.md).

## Architecture

```
[Svelte Web UI] ──/api──> [FastAPI] ──enqueue──> [RQ + Redis] ──> [Worker]
                              │                                      │
                              └──────────────── [SQLite] <──────────┘
                     (videos, jobs, transcripts, chat_signals, topics)
[Disk] ← audio files (temporary retention; scheduled cleanup)
```

- **1 RQ job per video** runs the stages sequentially (`worker/jobs.py:process_video`),
  reusing `run_stages` (same logic/resilience as the CLI) with `DbStore` (database cache) and
  `DbReporter` (live status/progress).
- **Pay-once cache backed by the database:** re-enqueueing skips stages whose artifact already
  exists (transcript/topics) — nothing already completed/paid for is reprocessed.
- **UI progress = polling** (~2 s) — chosen for simplicity/robustness (single-user); status and
  progress already live in the database.
- **GPU concurrency:** the transcription stage acquires a **distributed lock (Redis)**
  (`primocut:gpu`), guaranteeing **1 transcription at a time** regardless of how many `primocut
  worker` processes run. So bringing up N workers is **safe by construction**: the light stages
  (download/chat/analysis) of different videos overlap, but only one enters the GPU. Lock `timeout`
  (auto-releases if a worker dies): `PRIMOCUT_GPU_LOCK_TIMEOUT` (default 2h).
- **Degradation (FALLBACKS.md) preserved** and reflected in the UI: a degradable failure → report
  with a "⚠️ Degraded run" section; an essential failure (audio/transcription) →
  `Video.error`/`Job.failed`.

## Database (SQLite)

Tables: `videos, jobs, transcripts, chat_signals, topics, schema_migrations`.
Simple migrations: base schema via `create_all` (v1) + `db/migrations/NNN_*.sql` deltas.
- `Video.status`: `queued → downloading → transcribing → analyzing → done | error`; **`stopped`** when interrupted (via `stop`). Reprocessing (`reenqueue`) reuses the cache; deleting (`DELETE`) removes everything and requires resubmitting the URL to start from scratch.
- Audio = file on disk (deletable). Transcript/topics/signals = **permanent** (database).

## Prerequisites

- **Redis** (queue): `sudo apt install redis-server` or `docker run -p 6379:6379 redis`.
- **Node** (UI build): already validated (v22). Python deps via `/usr/bin/python3 -m pip --python .venv/bin/python install -r requirements.txt`.
- `primocut doctor` validates GPU/FFmpeg/yt-dlp/key/SDK **+ Redis + Database**.

## How to run (local production)

```bash
# 1. build the UI (once, or whenever the frontend changes)
cd frontend && npm install && npm run build && cd ..

# 2. Redis up (see above), then in separate terminals:
source .venv/bin/activate
primocut worker      # processes the queue (needs Redis and a GPU for real transcription)
primocut serve       # API + UI at http://127.0.0.1:8000
```
Open the browser at `http://127.0.0.1:8000`: submit a URL, follow the live status in the list,
open the report (macro topics, chat activity with peaks, searchable transcript, clickable
timestamps, 🔥 on items that coincide with peaks), mark cuts and export JSON. The report screen
also embeds the official YouTube player (sticky at the top): clicking a topic's **Watch** button
seeks the player to that timestamp. Topics live inside a collapsible card (expand/collapse all).

## Frontend development

```bash
cd frontend && npm run dev     # Vite on :5173, proxy /api → :8000 (run `primocut serve` alongside)
```

## API (summary)

| Method | Route | Action |
|---|---|---|
| POST | `/api/videos` `{url}` | validates, creates Video(queued) + Job, enqueues |
| GET | `/api/videos` | list with status/progress |
| GET | `/api/videos/{id}` | detail (video + job) |
| GET | `/api/videos/{id}/report` | report JSON (topics/chat/transcript, 🔥, links) |
| POST | `/api/videos/{id}/topics/{tid}` `{status}` | mark/unmark (new/marked/discarded) |
| GET | `/api/videos/{id}/export?format=json` | download the marked cuts (JSON) |
| POST | `/api/videos/{id}/reenqueue` | reprocess (idempotent; resets status→queued) |
| POST | `/api/videos/{id}/stop` | interrupts processing (cancels the job/worker; status→stopped) |
| DELETE | `/api/videos/{id}` | deletes EVERYTHING about the video (database + files); refuses (409) if active — stop it first |

### Export format (JSON)
```json
{ "video_id": "...", "url": "...", "title": "...", "generated_at": "...",
  "cuts": [ { "title": "...", "start_seconds": 0, "end_seconds": 20,
              "start_hms": "0:00", "end_hms": "0:20",
              "youtube_url": "https://youtube.com/watch?v=ID&t=0", "score": 0.7 } ] }
```

## Scheduled cleanup

```bash
primocut cleanup                 # one-shot (good for cron); deletes audio > N days
primocut cleanup --days 3
primocut cleanup --schedule      # registers a daily cleanup on rq-scheduler (run an `rqscheduler`)
```
Only deletes `*.wav/*.m4a/*.mp3`; **never** touches transcripts/topics/signals (permanent in the database).

## What carries over from Phase 0
`primocut doctor/run/models` remain operational. The `run` CLI uses `FileStore` (JSON cache) +
Markdown report, identical behavior. Multi-provider LLM (Anthropic/OpenAI/OpenRouter) and the
golden rule (the LLM selects indices, never generates timestamps) preserved.

## Tests (you run them)
`pytest -q` covers: migrations/DAO, job state transitions, **idempotent re-enqueue (cache
respected)**, API endpoints, cut marking and export, and cleanup (does not delete recent audio,
never deletes a transcript). No real Redis/GPU needed (fakes + inline execution).
