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
**per-project conditional distributions** of **packaging attributes** (persisted in
`video_attributes`) among outliers vs the population, gated by a **minimum-occurrence
guard**. **Shorts and long-form are never blended**: they monetise differently, target
different audiences and are different formats, so every population a dashboard reports —
baselines, outlier rankings, engagement medians, patterns, resurgences — is keyed by
`"long"`/`"short"` (`service.FORMATS`), and the UI picks one with a format tab. Each format
clears the sample guards on its own, so a thin format reports nothing rather than borrowing
the other's support. The two free artifacts (syntactic attributes + packaging impact) are written by the
snapshot cycle and by `primocut metrics refresh`; the dashboards stay pure reads.

**AI packaging enrichment** is in place, and is **opt-in** (guideline 9 — it costs an LLM
call). A **thumbnail** pass sends the image to a **vision model** under forced tool use (same
structured-output technique as the topic analysis) and records faces + dominant expression,
overlaid text + word count + transcription, dominant colors, contrast, arrows/circles and
confrontation framing; a **title** pass records people named, theme, tone and quoted speech.
Both are triggered only by hand — the **button on a video detail**, the **"Enrich outlier
thumbnails/titles"** batch on the project dashboard, `POST /api/channels/{cid}/videos/{vid}/enrich`
/ `POST /api/projects/{pid}/enrich` `{kind}`, or `primocut metrics enrich`. Results are
**paid for once**: each row is keyed by a **content hash** (the title string; the thumbnail's
**image bytes**, reusing the hash the snapshot cycle already tracks), so re-clicking costs
nothing and a channel in two projects is enriched once. Each row stores the **raw** model
output plus **boolean facets derived in code**, which is what feeds the conditional
distributions — measured **per source**, against the population of videos actually analysed
for it, so an un-enriched video counts as *unknown*, never as "attribute absent". The batch
targets the **definitive outliers only** (the same set the patterns read), runs on the queue
with **per-video isolation** (one failure never aborts it) and surfaces the error on the row
itself. Multi-provider is preserved (Anthropic default); `PRIMOCUT_ENRICH_TITLE_MODEL` /
`PRIMOCUT_ENRICH_VISION_MODEL` optionally point a pass at a cheaper model.

**Comments** close Phase 2, and are split by cost. **Collection is automatic**: each
snapshot cycle asks which videos are definitive outliers *now* and fetches their top
comments once (`commentThreads`, `order=relevance`, `PRIMOCUT_YOUTUBE_COMMENT_PAGES`
pages of 100 = 1 quota unit each, folded into the cycle's total) into `video_comments`.
It is **level-triggered, not edge-detected** — no score is stored to diff against, so
each cycle re-asks and skips whatever `competitor_videos.comments_state` settles
(`done`/`disabled` are terminal; `error` retries next cycle). **Synthesis is manual**
(guideline 9): one forced-tool-use pass over the *already stored* comments records
recurring themes, the audience's **explicit requests** (guest/topic/format/other) and
overall sentiment into `video_enrichments` (`kind=comment_synthesis`) — triggered by the
video-detail button, the project-dashboard batch, `POST …/enrich {kind:"comments"}` or
`primocut metrics enrich --kind comments`, never by the scheduler. The **golden rule
covers quotes**: comments reach the model **indexed** and it returns only an
`example_index`, which the code resolves and **clamps** back to a real stored comment —
it cannot invent audience words, exactly as it cannot invent a timestamp. Paid once by a
content hash over the comment set; **Regenerate** re-fetches then re-reads. The project
dashboard aggregates the requests **in code** (no second LLM pass), merged on a
normalised key and ranked by *how many videos asked*.

**Phase 2 UI** is in place: a **Competitors** section in the SPA (`#/projects…`), where a
**project selector** (create/rename/remove — removing detaches channels and says the data is
kept) scopes everything below it. The **selected project lives in the URL**, so no global store
was introduced and every screen survives a reload. Inside a project, two tabs: **Channels**
(register by URL/@handle/ID, live **backfill progress** via the existing 2 s polling — guarded
so it stops when terminal — and detach) and **Dashboard**, the niche view (outlier ranking with
score/band/**VPH**, per-project attribute patterns, packaging log with a before/after verdict,
resurgence alerts). Drilling down: a **channel dashboard** (baselines, engagement, cadence
histograms, outliers, packaging, resurgences) and a **video detail** (snapshot time series,
outlier/VPH/engagement, syntactic attributes, packaging log, YouTube link). Every section that
reports a population is scoped to one format by a **`FormatTabs`** control (Long-form/Shorts,
with counts) — never a blended figure. Methodology guards
are surfaced rather than hidden: a **provisional** pill, an **insufficient data** note, a `—`
where VPH cannot be computed, a suppressed-pattern message, and — for a channel too fresh to
score — **"Collecting data — provisional scores from tomorrow."** instead of an empty screen.
Charts are hand-built (inline SVG / CSS bars); no chart library was added. The video detail
also carries the two **opt-in AI panels** (analyse/re-analyse, a live state while the call
runs, and the failure message in place of a blank panel) and the **Audience comments** panel
(the collected count, then synthesise/re-synthesise + **Regenerate**, the themes and requests
each shown with a **real quoted comment**, or the note that comments are disabled); the
project dashboard carries the batch actions, tags each reported pattern with its **source**,
and shows **Audience requests** as one column per request type the niche actually used.

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

The backfill also writes the channel's **first snapshot**, so the dashboards have data as soon
as the channel is added rather than staying empty until the first scheduled cycle (up to 24h).
The per-video counters are free — they ride along in the `videos.list` response the backfill
already makes; only the channel statistics add a call (1 unit). Views-per-hour and resurgences
still need a **second** snapshot: they come from deltas between snapshots and are never
fabricated from totals, so they stay empty until the first cycle runs.

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

The same three views are exposed over HTTP — and are what the **Competitors** UI reads:
`GET /api/projects/{pid}/dashboard`,
`GET /api/channels/{cid}/dashboard`, `GET /api/channels/{cid}/videos/{vid}`. Baseline window,
brackets, provisional/resurgence/impact/pattern thresholds are all configurable
(`PRIMOCUT_OUTLIER_*`, `PRIMOCUT_VPH_*`, `PRIMOCUT_RESURGENCE_*`, `PRIMOCUT_PACKAGING_IMPACT_*`,
`PRIMOCUT_PATTERN_*` — see `.env.example`).

**AI packaging enrichment — opt-in, and it COSTS an LLM call** (guideline 9). It never runs on
the scheduler nor inside `metrics refresh`; only these do it:

```bash
# The niche batch: the project's DEFINITIVE outliers (the set the patterns read)
primocut metrics enrich --project <PID> --kind thumbnail
primocut metrics enrich --project <PID> --kind title

# A single video
primocut metrics enrich --video <VID> --kind thumbnail
```

The CLI runs inline (no worker needed); the UI buttons and the two endpoints
(`POST /api/channels/{cid}/videos/{vid}/enrich`, `POST /api/projects/{pid}/enrich`, both
`{kind: thumbnail|title}` → 202) go through the queue, so `primocut worker` must be running.
Either way the work is **paid for once**: a video is re-analysed only when its content hash
changes (the title string; the thumbnail's image bytes), and the result is shared by every
project tracking the channel — re-running a batch reports the untouched ones as *cached*.
The thumbnail pass needs a **vision-capable** model and a `thumbnail_hash` already collected
(run a snapshot cycle first). By default every pass uses the selected provider's configured
model; point one at a cheaper model with `PRIMOCUT_ENRICH_TITLE_MODEL` /
`PRIMOCUT_ENRICH_VISION_MODEL` / `PRIMOCUT_ENRICH_COMMENTS_MODEL` (a model id is
provider-specific — only valid for the provider in use).

**Comments: collected free, synthesised on demand.** Nothing extra to run — the snapshot
cycle collects the top comments of every **definitive outlier**, once per video, spending
`PRIMOCUT_YOUTUBE_COMMENT_PAGES` (2) quota units and **no** LLM call. Reading them is the
opt-in half:

```bash
primocut metrics enrich --video <VID> --kind comments      # one video
primocut metrics enrich --project <PID> --kind comments    # the project's definitive outliers
primocut metrics enrich --video <VID> --kind comments --regenerate   # re-fetch, then re-read
```

Same triggers over HTTP/UI as the packaging passes (`{kind: "comments"}`; the video detail's
**✨ Synthesise comments**, the dashboard's **✨ Synthesise outlier comments**). Without
`--regenerate` the pass is **paid once**: it is skipped for free while the collected comment
set is unchanged. A video with comments **disabled** is recorded as such and never asked
again — synthesising it fails with that message rather than calling the LLM. The project
dashboard's **Audience requests** merges every synthesis of the project (in code, no extra
LLM call), ranked by how many videos asked for each thing.

### 2.9 Competitors UI — manual smoke test

The Phase 2 UI has no automated coverage (the suite is Python-only), so it is validated by
hand. Build the UI and start `primocut serve` (plus Redis + `primocut worker` for a real
backfill), then open `http://127.0.0.1:8000/#/projects`:

1. **Projects.** With an empty database the section opens on **"Create your first project"**.
   Create one → the selector appears and the URL becomes `#/projects/{pid}`. **Manage** →
   rename (the selector updates) and remove (confirms *"Channels are detached; all channel
   data is kept."*). Reloading any deep URL restores the same screen (selection is in the URL,
   not in memory).
2. **Channels tab.** Add a channel by URL/@handle → the row appears with a live progress bar
   (`done/total`, polled every 2 s) that settles when the backfill ends; re-adding a channel
   already tracked reports *"already tracked, no new collection"*. Detach asks for
   confirmation and keeps the data.
3. **Dashboards.** With snapshots collected (`primocut competitors snapshot` a couple of times,
   then `primocut metrics refresh`), check the niche **Dashboard** (ranked outliers with
   score/band/VPH, patterns, packaging log with the video title + before/after verdict,
   resurgences), the **channel dashboard** (baselines, engagement, cadence, packaging) and a
   **video detail** (snapshot chart, attributes, working *Watch on YouTube*).
4. **AI enrichment (opt-in — this spends money).** With a provider key set and `primocut worker`
   running, open a video detail → **✨ Analyse thumbnail with AI** → the panel shows
   *Analysing the thumbnail…* and settles into the attributes; the button becomes
   **Re-analyse**. Click it again with the thumbnail unchanged: nothing is recharged (the
   worker log says `0 enriched, 1 cached`). Same for the title. On the project dashboard,
   **✨ Enrich outlier thumbnails** reports how many outliers it queued; the patterns table
   grows AI rows tagged `thumbnail_llm` once enough outliers are enriched to clear the
   minimum-occurrence guard. To see the failure path, point `ANTHROPIC_API_KEY` at a bad key:
   the panel must render the error text and the polling must stop.
5. **Comments (collection is free; only the synthesis spends).** After a snapshot cycle in which
   a video sits at ≥3x, its video detail must show *"N top comments collected automatically (no
   AI cost)"* — and a video below 3x must show none (the collection is deliberately outlier-only).
   **✨ Synthesise comments with AI** → *Synthesising the comments…* → themes and requests, each
   with a **real quoted comment**; the button becomes **Re-synthesise** and clicking it again
   charges nothing (`0 enriched, 1 cached`), while **Regenerate** re-fetches and re-reads. A
   video with comments disabled must say so and offer no button. The project dashboard's
   **Audience requests** must merge the same request across videos into one row with its video
   count.
6. **Guard states (the important ones).** A channel too fresh to score must show
   **"Collecting data — provisional scores from tomorrow."** instead of an empty dashboard; a
   video under 7 days must carry the amber **provisional** pill; a format with too few peers
   must read **insufficient data**; a video with a single snapshot must show **`—`** for VPH
   (never a fabricated `total_views ÷ age`); a small pattern sample must stay suppressed.

To exercise the states without waiting for real collection, point the server at a throwaway DB
(`PRIMOCUT_DB_PATH=/tmp/smoke.db primocut serve`) and seed it through `primocut.db.competitors`
(`upsert_channel`/`link_channel`/`upsert_video`/`add_video_snapshot`) — two snapshots on a video
are enough for a VPH, and ≥10 same-format peers for a baseline.

### 2.10 Tests

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
**quota accounting** (fully faked, no network), channel resolution, backfill of 120, the
backfill's **first channel+video snapshots** (shared `captured_at`, degrading when the channel
stats fail), **cross-project dedup**, detach/delete **data preservation**, **orphan
deactivation**, and the projects/competitors API + CLI. Phase 2 continuous collection: **frequency selection**
(daily vs 6h), snapshot recording, **new-video detection**, **title change**, **thumbnail
change by byte hash under an unchanged URL**, **per-channel isolation**, orphan exclusion,
per-cycle quota, and idempotent scheduler registration (all faked, no network). Phase 2 metrics
engine (synthetic fixtures, no network): **median baseline robust to a viral in the window**,
**separate Shorts/long-form baselines**, **provisional vs definitive**, **insufficient_data**,
**VPH from deltas** (and `None` — never fabricated — with a single snapshot), **resurgence**,
**packaging before/after** (persisted only once the window elapses), **minimum-occurrence guard
suppressing a small-sample pattern**, and a **channel shared by two projects yielding identical
metrics with scoped reports**. Phase 2 AI enrichment (LLM faked, no key/network):
**pay-once cache by content hash** (an unchanged hash calls nothing and fetches no bytes; a new
hash recomputes — both kinds), **sharing across projects**, **per-video failure isolation in a
batch** (with the error recorded and retried on the next click), **the empty-thumbnail-hash
refusal**, **the batch targeting definitive outliers only** (a provisional 10x is excluded),
**AI attributes in the conditional distributions** with the **per-source population** (an
un-enriched video is *unknown*, not "attribute absent" — the test pins the honest lift of 1.0
against the fabricated 3.0), **suppression while enrichment is sparse**, the **vision plumbing
of each provider** (base64 block vs data-URL, forced tool use, model override), and — the cost
guard — **a full `snapshot_cycle` + `metrics refresh` writing no LLM-sourced row**. Phase 2
comments (API + LLM faked, no key/network): **the collection trigger** (a 2x video is not
touched; the same video at 3x is), **collect-once**, **the paged fetch** (page cap, `pageToken`
threading, early stop) with **its quota folded into the cycle's total**, **comments disabled
degrading to a terminal state** (the cycle completes and never asks again) vs. **a fetch error
retried next cycle**, the **pay-once synthesis cache** and **explicit regeneration** (re-fetch +
re-read), **`regenerate` rejected for the packaging kinds**, **refusing to synthesise with
nothing collected** (no LLM call), the **model override**, the **golden rule for quotes** (an
out-of-range `example_index` clamps to a real comment), the **project-scoped request
aggregation** (spelling variants merge; another project never leaks in), and — the cost guard —
**a full cycle over a 10x outlier collecting comments while writing no synthesis row**.

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
collection time series `channel_snapshots, video_snapshots, packaging_changes`, the append-only
Radar log `events`, the metrics
artifact `video_attributes` (persisted packaging features, unique per `(video_id, source)`:
`syntactic` = free/deterministic, `title_llm`/`thumbnail_llm` = the opt-in AI enrichment, each
content-hash gated and carrying its own `state`/`error`), and the comment pair
`video_comments` (raw top comments of outliers, unique per `(video_id, comment_id)`, collected
automatically — the collection state lives on `competitor_videos.comments_state`) +
`video_enrichments` (the opt-in LLM syntheses, unique per `(video_id, kind)`, today only
`comment_synthesis`; same content-hash/`state`/`error` contract as `video_attributes`, but a
**separate table on purpose** — `metrics.patterns` reports every boolean of an attributes row
as a packaging pattern, and a narrative synthesis must not reach the distributions).
The append-only `events` table (index `(category, occurred_at DESC)`) is the Radar fact log:
niche facts (channel-scoped, `project_id` NULL, attributed on read) and ops failures
(project-scoped, `severity=error`, cleared via `resolved_at`). It is the one derived-figure
exception to "computed on read" — a threshold crossing is a moment a recompute would erase, so
it is recorded when it happens (see §Radar events below). `competitor_videos.last_band_emitted`
backs the anti-flapping guard.
Outlier scores, VPH, resurgence and pattern distributions are computed on read over the
snapshots — never stored; the only persisted derived figures are `video_attributes`,
`video_enrichments`, `packaging_changes.vph_before/after` and the `events` crossing facts.
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
  polled like the video pipeline; the backfill runs once per channel, globally, and writes that
  channel's first `channel_snapshots`/`video_snapshots` rows. Every **later** snapshot comes
  from `snapshot_cycle`, which stays the sole continuous collector.

- **Snapshot scheduler:** `primocut scheduler` runs rq-scheduler, which registers two cron jobs
  (idempotent, cancel-by-id) of the same `snapshot_cycle(scope)` function — `scope="all"` daily,
  `scope="recent"` every 6h — and moves due jobs onto the `primocut` queue, where the worker
  executes them. The daily hour is offset from the 6h grid so the two never fire in the same
  minute. Selection: `all` → every video of active channels; `recent` → the same, filtered to
  `published_at >= now - youtube_recent_days`. One `YouTubeClient` per cycle gives a per-cycle
  quota total (logged); channels are processed in isolation. After the channel loop the cycle
  runs three **isolated steps**: the metrics artifacts (deterministic, offline); **event
  emission** (`worker/event_emission.py`) that records `outlier_crossed` for videos stepping up
  a band, the **single** crossing-detection point, with the anti-flapping guard; and the
  **comment collection** (network + quota), which is now a **consumer** of those events — it
  reads the videos with an `outlier_crossed` event and skips whatever `comments_state` settles,
  so there is one source of truth instead of two detectors. It still retries an `"error"` video
  even without a fresh crossing (`comments_state` is non-terminal). Finally the cycle appends a
  born-resolved `cycle_done` event carrying the quota accounting the activity log reports. Code:
  `worker/scheduler.py`, `worker/snapshot_jobs.py`, `worker/event_emission.py`,
  `worker/comment_collection.py`.

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
| GET | `/api/projects/{pid}/dashboard` | niche outlier ranking, per-project attribute patterns (guarded), aggregated audience requests, packaging with before/after impact, resurgences — `outliers`/`patterns`/`resurgences` keyed `long`/`short` |
| GET | `/api/channels/{cid}/dashboard` | baselines, outlier ranking, engagement medians, cadence, resurgences — `baselines`/`outliers`/`engagement`/`resurgences` keyed `long`/`short` |
| GET | `/api/channels/{cid}/videos/{vid}` | snapshot series, VPH (current + pairwise), outlier score, engagement, resurgence, attributes per source (syntactic + the AI ones with their state/error), collected-comment count/state, the comment synthesis (or null), packaging changes |

Phase 2 AI enrichment (**opt-in — costs an LLM call**; enqueues only, never calls the LLM
inside the request):

| Method | Route | Action |
|---|---|---|
| POST | `/api/channels/{cid}/videos/{vid}/enrich` `{kind, regenerate?}` | enrich one video's `thumbnail`/`title`, or synthesise its `comments` (202; 400 on an unknown kind, or on `regenerate` for a packaging kind — it has nothing to re-fetch) |
| POST | `/api/projects/{pid}/enrich` `{kind}` | enrich the project's **definitive outliers** (202; returns how many were queued) |

Phase 2 Radar (the append-only event log; reads are bounded and index-backed):

| Method | Route | Action |
|---|---|---|
| GET | `/api/events?category=niche&project_id=&since=&limit=` | niche feed, newest-first, each event carrying its project **badges** (resolved on read); `since` is a lower bound, `project_id` scopes to that project's channels |
| GET | `/api/alerts` | open operational failures (`category='ops' AND resolved_at IS NULL`) |
| GET | `/api/activity` `?limit=&before_id=` | full paginated log (`next_before_id` cursor) + `quota_units` accounting summed from `cycle_done` events |
| POST | `/api/events/seen` `{ids?, until?}` | mark niche events seen by id set and/or up to a timestamp; ops events are never marked (they resolve, not dismiss) |

### Golden rule — timestamps are never invented

The LLM receives **indexed** segments (`[i] M:SS text`) and returns only
`start_index`/`end_index` (forced tool use); the code resolves the indices → seconds from the
transcript and **clamps** out-of-range indices. It is *impossible* for the LLM to invent a
time. This holds in both the CLI and the web app.

The **comment synthesis applies the same rule to quotes**: the comments arrive indexed
(`[i] (N likes) text`) and the model returns only an `example_index`, which
`metrics/comments.py:resolve_example` resolves and clamps back to a stored comment. Every
quoted word on screen is a word the audience actually wrote.

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
  (`core.py` = DB-free methodology; `attributes.py`/`impact.py` = the free persisted artifacts;
  `enrichment.py` = the opt-in AI passes (hash gate per kind, derived facets, store dispatch);
  `comments.py` = the synthesis hash/prompt/quote resolution + the request aggregation;
  `patterns.py` = per-project distributions; `service.py` = dashboard payloads + the batch and
  per-channel outlier targets). HTTP routers: `api/metrics.py` (channel/video + single-video
  enrich) + the project dashboard and batch enrich in `api/projects.py`.
- AI enrichment (tool schemas + job): `src/primocut/analysis/enrichment_prompts.py`,
  `src/primocut/worker/enrich_jobs.py` (per-video isolation; one function for all three kinds),
  enqueued by `worker/queue.py:enqueue_enrichment`. Vision (base64/data-URL blocks) and the
  per-call model override live in `analysis/llm.py`, behind the same `SelectionLLM` protocol.
- Comment collection (automatic, inside the snapshot cycle):
  `src/primocut/worker/comment_collection.py`; the API call is
  `youtube/client.py:list_top_comments`.
- Competitor channel registration/backfill (queue + job): `src/primocut/worker/channels.py`,
  `src/primocut/worker/channel_jobs.py`
- Snapshot collection (cycle job + scheduler): `src/primocut/worker/snapshot_jobs.py`,
  `src/primocut/worker/scheduler.py`
- Projects/competitors API router: `src/primocut/api/projects.py`
- Frontend (Svelte SPA): `frontend/src/` — hash routing + sidebar in `App.svelte`, the single
  API client in `lib/api.js`, formatting helpers in `lib/format.js`, one global stylesheet
  `app.css` (design tokens; components carry no `<style>` blocks). Phase 2 UI:
  `views/Competitors.svelte` (project shell: selector + sub-route dispatch) over
  `views/{ProjectDashboard,ProjectChannels,ChannelDashboard,VideoDetail}.svelte`, with
  `components/{ProjectSwitcher,OutlierTable,Sparkline}.svelte`.

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
| **Channel backfill** | The first channel snapshot fails (`channels.list`) | Isolated in its own step: logged and skipped; the uploads and the `done` state are kept (they are the point of the backfill). The next cycle records the channel stats |
| **Snapshot cycle** | A channel's API/thumbnail call fails | **Per-channel isolation**: the error is logged and the cycle moves to the next channel; it never aborts the whole cycle nor lets the exception crash the scheduler |
| **Snapshot cycle** | A thumbnail cannot be fetched/hashed | Empty hash; no spurious packaging-change event (a change needs both old and new hashes non-empty) |
| **Snapshot cycle** | Channel deactivated (orphaned) | Excluded from selection — collection stops until the channel is re-linked (`active=true`) |
| **Snapshot cycle** | Metrics-artifact persistence fails (attributes/impact) | Isolated in its own step: logged and skipped, **never affecting the collected snapshots** of the cycle |
| **Comment collection** | The uploader disabled comments (HTTP 403 `commentsDisabled`) | Recorded as the **terminal** `comments_state="disabled"` and skipped: it is a permanent fact, not a failure, so the cycle stops asking. The UI says so; a synthesis refuses with that message instead of calling the LLM |
| **Comment collection** | Any other fetch failure (quota, network, malformed page) | **Per-video isolation**: logged, `comments_state="error"` with the message, the cycle continues. Not terminal — the next cycle retries that video |
| **Comment collection** | Selecting the outliers fails | Isolated in its own step: logged, zero collected, **never affecting the collected snapshots** |
| **Comment synthesis** | No comments collected / collection disabled or errored | Refuses **without calling the LLM**, and the row's error says which of the three it was (there is nothing to hash, so every click would recharge) |
| **Comment synthesis** | Model returns an off-schema payload or an out-of-range `example_index` | Every field is read defensively (missing → neutral) and each index is **clamped** to a real stored comment, so a sloppy response degrades the payload instead of breaking the row or inventing a quote |
| **Metrics dashboards** | Too few videos / a single snapshot | **Degrade, not crash**: an outlier score becomes `insufficient_data`, a VPH becomes `None`, a small-sample pattern is suppressed — the dashboard still renders |
| **AI enrichment** | One video's LLM/thumbnail call fails | **Per-video isolation**: logged, the row is marked `state="error"` with the message (which the UI renders in place of a blank panel), and the batch continues. The content hash is cleared so the next explicit click retries only that video |
| **AI enrichment** | The thumbnail was never byte-hashed (`thumbnail_hash=""`) | Refuses without calling the LLM: with no cache key every click would recharge. Surfaces as the row's error, asking for a snapshot cycle first |
| **AI enrichment** | Model returns an off-schema/partial payload | The derived facets read every field defensively (missing → the neutral value), so a sloppy response degrades the facets instead of breaking the row |

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
| `PRIMOCUT_YOUTUBE_SHORT_MAX_SECONDS` | Duration threshold for the `is_short` heuristic (default 180) |
| `PRIMOCUT_YOUTUBE_BACKFILL_LIMIT` | Uploads collected on a new channel's backfill (default 120) |
| `PRIMOCUT_YOUTUBE_COMMENT_PAGES` | Pages of top comments collected per outlier (default 2 = up to 200 comments, 1 quota unit per page). Costs no LLM call |
| `PRIMOCUT_SNAPSHOT_DAILY_CRON` / `PRIMOCUT_SNAPSHOT_RECENT_CRON` | Snapshot cadences (daily / 6h) |
| `PRIMOCUT_OUTLIER_WINDOW_VIDEOS` / `PRIMOCUT_OUTLIER_WINDOW_DAYS` | Baseline rolling window (smaller of the two) |
| `PRIMOCUT_OUTLIER_MIN_SAMPLE` / `PRIMOCUT_OUTLIER_PROVISIONAL_DAYS` / `PRIMOCUT_OUTLIER_BRACKETS` | Insufficient-data guard, provisional cutoff, bracket tiers |
| `PRIMOCUT_VPH_CURRENT_WINDOW_HOURS` | Trailing window for the "current" VPH (default 24) |
| `PRIMOCUT_RESURGENCE_K` / `PRIMOCUT_RESURGENCE_MIN_AGE_DAYS` / `PRIMOCUT_RESURGENCE_BASELINE_DAYS` / `PRIMOCUT_RESURGENCE_MIN_VPH` | Resurgence detection |
| `PRIMOCUT_PACKAGING_IMPACT_WINDOW_DAYS` | Before/after window for packaging impact (default 7) |
| `PRIMOCUT_PATTERN_MIN_OCCURRENCE` / `PRIMOCUT_PATTERN_OUTLIER_BRACKET` | Pattern minimum-occurrence guard + outlier threshold (the latter is also the batch-enrichment target) |
| `PRIMOCUT_ENRICH_TITLE_MODEL` / `PRIMOCUT_ENRICH_VISION_MODEL` / `PRIMOCUT_ENRICH_COMMENTS_MODEL` | Model override for the opt-in enrichment passes. **Empty (default) = the selected provider's configured model**; a model id is provider-specific, and the vision one must support image input |

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
- **Phase 2 — Competitor channel modeling ✅ (done).** Given a **user-provided** set of competitor
  channels, continuously collect **public** performance data and reveal what is working in the
  niche — outlier videos, packaging patterns (title/thumbnail), posting strategy and audience
  demand — to guide PRIMOCUT's editorial decisions. Delivered across six steps: the projects +
  global-channel foundation, continuous snapshot collection, the deterministic metrics engine,
  the Competitors UI, the opt-in AI packaging enrichment, and comments (automatic collection +
  manual synthesis). Channels are supplied by the user (no niche-wide channel discovery).
  **Out of scope:** anything that depends on private YouTube Analytics (CTR, retention,
  impressions — unavailable for third-party channels); clip generation/WhisperX (now Phase 3).
  See the [2026-07-14 decision](#2026-07-14--phase-2-redefined-competitor-channel-modeling-cuttingpublishing-shift-to-34).
  **Deferred extension point — transcribing competitor outliers:** the pipeline that already
  exists (`run_stages` → yt-dlp audio → faster-whisper under the Redis GPU lock `primocut:gpu`)
  would be reused as-is against a `competitor_videos` row, to mine what a winning video actually
  *says*. Not built: the pipeline is keyed to the `videos` table (the live-report entity), so it
  needs that split resolved first, and each outlier costs real GPU minutes — it waits for a
  demand signal from the niche views now shipped.
- **Phase 3 — Assisted cutting (next).** Automatic clip generation with FFmpeg; evaluate
  **WhisperX** (word-level timestamps + diarization). The `TranscriptionEngine` interface
  exists precisely so WhisperX can be plugged in without touching the rest of the system.
- **Phase 4 — Publishing.** AI titles/descriptions, vertical format, upload via the YouTube
  API, multi-user.

---

## 6. Decision log

The most important section. **Never overwrite or delete an entry**: a reverted decision gets a
**new** entry pointing to the reversal. Dates in ISO (`YYYY-MM-DD`).

### 2026-07-15 · Comments: automatic collection, manual synthesis (Phase 2 complete)
**Type:** technical + product (methodology — guidelines 9 and 10).
**Decision:** deliver the last Phase 2 promise by splitting comments **along the cost line**, and
close the phase. The policy itself (collection automatic, synthesis manual) had been stated in
`PRIMOCUT.md` since the phase was scoped but never recorded here — this entry is also that
missing record. Key decisions:
- **Collection is level-triggered, not edge-detected.** The obvious reading of "collect when a
  video *crosses* 3x" is a rising edge — but guideline 10 stores no score, so there is nothing to
  diff against, and re-deriving a historical baseline to fake one would be a large amount of
  machinery for a worse answer. Instead each cycle asks "is it a definitive outlier **now**?"
  (`definitive_outlier_ids_for_channel`, reusing `_is_definitive_outlier`) and lets
  `competitor_videos.comments_state` stop the repeat. That is idempotent, self-heals across a
  missed cycle, and is the same shape as `refresh_syntactic_attributes`. The state lives on the
  video row, not in a new table: it models a once-per-video operation, exactly like
  `backfill_state` on the channel row.
- **The target is the DEFINITIVE outliers**, so a provisional video (< 7 days) waits. It costs a
  week of latency and buys two things: no quota on a video whose score may still fall back, and a
  richer comment set — a single collection on a day-old video would capture the thread before it
  filled. It is also the same set the batch enrichment and the pattern math already use.
- **The golden rule extended to quotes.** Comments reach the model **indexed** and it returns an
  `example_index`, never text; `resolve_example` maps it back to the stored comment and
  **clamps**. Timestamps were protected this way since 2026-07-12 for exactly this reason, and a
  fabricated audience quote is the same class of lie as a fabricated timestamp — it looks like
  evidence. Letting the model echo the text would have been one less function and no guarantee.
- **The synthesis lands in `video_enrichments`, not `video_attributes`.** `metrics.patterns`
  reports **every boolean** of an attributes row as a packaging pattern; a narrative synthesis in
  that table would have leaked into the conditional distributions. A second table, with the same
  `content_hash`/`state`/`error` contract, keeps `ATTR_SOURCES` — and the distributions —
  untouched. It is the mirror image of the step-5 per-source reasoning: there, honesty meant
  measuring each source against its own population; here it means not measuring this one at all.
- **Reuses the enrichment machinery whole.** `comments` is a third `kind` through the same
  `enqueue_enrichment` → `enrich_videos` → `plan/run/save` path, so it inherits one isolation
  story, one polling surface and one cache gate; only the store and the prompt differ.
  **`regenerate` is rejected, not ignored, for the packaging kinds**: they have nothing to
  re-fetch, so the flag could only mean the caller expected something we would not do.
- **The aggregation is code, not a second LLM pass** (`request_key` = casefold + strip accents/
  punctuation), so it is free, auditable and re-tunable **without re-paying**. Ranked by *how
  many videos asked* rather than raw mentions: four videos asking for a guest is a niche signal,
  37 mentions on one video is one fandom. **No minimum-occurrence guard**, deliberately — that
  guard exists because a distribution over a small sample makes a false statistical claim, while
  this list claims only "N videos' comments asked for this", which is true at N=1; the count is
  on every row.
- **Disabled comments are a fact, not a failure.** 403/`commentsDisabled` → the terminal
  `comments_state="disabled"`, so the cycle stops asking forever; any other error stays
  retryable.
**Reason:** the packaging patterns say *how* winning videos are packaged; nothing said what the
niche's audience is actually asking for — the input that most directly picks the next guest or
subject. Guideline 9 is what keeps it affordable: the collection is quota (2 units/outlier) and
the LLM is only ever spent on a video the user pointed at.
**Rejected alternatives:** edge detection against a stored/derived historical score (machinery,
and it contradicts guideline 10); collecting on every video (quota on videos nobody will read);
collecting at the 3x crossing including provisional videos (pays for scores that may fall back,
and collects a thread that has not filled); the synthesis in `video_attributes` (would fabricate
packaging patterns out of narrative text); letting the model quote directly (invented evidence);
an LLM pass to merge the requests across videos (a paid, unauditable version of a `casefold`);
a minimum-occurrence guard on the request list (suppresses true statements — see above);
a separate `competitors enrich` command (a second door to `metrics enrich`).
**Deferred, explicitly:** transcribing competitor outliers — the extension point is recorded in
the [roadmap](#5-roadmap).
**Test note:** the API and the LLM are both faked, so the suite still needs no key and no
network; the cost guard is itself a test (a full cycle over a 10x outlier collects comments and
writes **no** synthesis row). The UI stays on the documented manual smoke test
([§2.9](#29-competitors-ui--manual-smoke-test)).

### 2026-07-15 · Phase 2 AI enrichment: opt-in, hash-cached, measured per source
**Type:** technical + product (methodology — guidelines 9 and 10).
**Decision:** add the thumbnail (vision) and title (semantic) packaging attributes promised in
`PRIMOCUT.md`, reusing the analysis stack rather than growing a second one. Key decisions:
- **The tool schema is the contract.** Both passes force a tool call (the topic analysis's
  technique) with the schema written in **Anthropic shape**, because `OpenAILLM.select()`
  already converts that shape to OpenAI's function format — so multi-provider survives with
  **zero per-provider branching** in the enrichment, and OpenRouter inherits it for free.
  Vision needed exactly one seam: an optional `images` argument on `select()`, packed as a
  base64 `image` block (Anthropic) or a `data:` URL `image_url` block (OpenAI-compatible). A
  `model` override rides along so a pass can point at a cheaper model.
- **The model observes; the code decides.** Rows store the **raw** output plus **boolean
  facets derived in code** (`thumb_multiple_faces` = ≥2 faces, `thumb_text_heavy` = ≥4 words,
  one-hot expression/tone/colors). `patterns.py` only counts booleans, so the facets are what
  reaches the distributions — and every threshold stays auditable and re-tunable **without
  re-paying** for the analysis. Asking the model for the booleans directly would have baked
  the thresholds into a prompt and into the cache.
- **Each source is measured against its own population.** `conditional_patterns` is called
  once per source with the population restricted to the videos analysed for it. Merging the
  sources into one dict per video would have been a **methodology bug**: an un-enriched video
  lacks the AI keys, `a.get(attr) is True` reads False, and it would count as *"attribute
  absent"* rather than *"unknown"* — deflating the population rate and **fabricating lift**
  (the test pins the honest 1.0 against the fabricated 3.0). The existing minimum-occurrence
  guard then does the rest on its own: while enrichment is sparse, no AI pattern is claimed.
- **Pay-once, keyed by content hash, shared across projects.** The title reuses
  `title_content_hash`; the thumbnail reuses **`competitor_videos.thumbnail_hash`** — the byte
  hash the snapshot cycle already maintains and change-tracks — so checking the cache costs no
  network, and bytes are fetched only once the hash says the work is due. An **empty** hash is
  refused rather than analysed: with no cache key every click would recharge.
- **Everything goes through the queue** (guideline 7), single video and batch alike: one job
  function over a list of one vs many means one code path, one isolation story and one thing
  to poll. The **batch targets the definitive outliers only** — the same set the patterns
  read, reusing `_is_definitive_outlier`; paying for a video the distributions never read is
  waste, and a provisional score may still fall back to normal (the content hash would not
  protect against that — the score changes, the bytes don't).
- **State lives on the attributes row** (`state`/`error`, migration `005`), not in a new
  table: the row is already unique per `(video_id, source)`, and this is the same shape as
  `backfill_state`, which the UI already polls. A failure leaves a visible message and an
  empty hash, so the next click retries **only** that video.
**Reason:** the pattern report could say *"outliers use ALL CAPS 2.1× more"* but not *"outliers
show a shocked face 3× more"* — the thing that actually drives editorial decisions. Guideline 9
is what keeps it affordable, and the per-source population is what keeps it honest.
**Rejected alternatives:** asking the LLM for booleans (bakes thresholds into the cache);
merging sources into one population (fabricates lift, see above); a synchronous single-video
endpoint (fewer moving parts, but two code paths and a second failure story for one saved
poll); a separate progress table (a batch is transient — the same reasoning that rejected one
for snapshot cycles); pre-filling `ENRICH_TITLE_MODEL=claude-haiku-4-5` (a model id is
provider-specific — it would break the default the moment the provider changed).
**Test note:** the LLM is faked throughout, so the suite still needs no key and no network; the
cost guard is itself a test (a full `snapshot_cycle` + `metrics refresh` must write no
LLM-sourced row). The UI stays on the documented manual smoke test ([§2.9](#29-competitors-ui--manual-smoke-test)).

### 2026-07-15 · Phase 2 UI: route-encoded project selection, guards shown not hidden
**Type:** technical + product.
**Decision:** build the Competitors UI inside the existing SPA, following its conventions rather
than introducing new ones. Key decisions:
- **The selected project lives in the URL** (`#/projects/{pid}/channels/{cid}/videos/{vid}`), not
  in a store. The codebase had **zero Svelte stores** — state is component-local, and the only
  global state is the hash route. Route-encoding keeps that property, makes every screen
  reload-safe and linkable, and the selector is simply a dropdown that rewrites the hash. The
  rejected alternative (a `writable` selectedProjectId + localStorage, with the selector pinned
  in the sidebar) would have introduced the app's first store to model something the URL already
  models.
- **Methodology guards are surfaced, never hidden.** Guideline 10 is only worth anything if the
  UI shows it: a **provisional** pill (amber `--cue`), an **insufficient data** note, a literal
  **`—`** where VPH is `None` (a single snapshot — never `total_views ÷ age`), a
  suppressed-pattern message, and **"Collecting data — provisional scores from tomorrow."** for a
  channel too fresh to score. A fresh channel must never render an empty dashboard.
- **No chart library.** The snapshot series is an inline **SVG** polyline (non-scaling stroke);
  cadence is CSS bars reusing the existing `.spark` well. Consistent with the chat-activity
  histogram and keeps the bundle light (~92 kB).
- **Three backend serialization tweaks, all pure reads, no methodology change.** The project
  dashboard's packaging rows carried the **channel** title where the UI needs the **video** title
  (a latent bug — the packaging log is per video); each ranked outlier now carries **current
  VPH** (reusing `core.current_vph`) so the niche ranking shows score/band/VPH; and the channel
  dashboard gained a **`packaging`** key (same shape as the project one, scoped to the channel).
  The third was not in the original approval but the channel dashboard is specified to show
  packaging changes, and the only alternative — fetching the whole project dashboard in the
  channel view just to filter it — would recompute every channel's scores on every channel page.
**Reason:** the metrics engine was already correct but invisible; the UI is what makes the niche
signal usable, and showing the guards is what keeps it honest.
**Test note:** the suite is Python-only, so the UI is covered by a **documented manual smoke
test** ([§2.9](#29-competitors-ui--manual-smoke-test)); the service tweaks only add JSON keys, so
the metrics suite (incl. the shared-channel test) stays green.

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
  default 180 = YouTube's own Shorts ceiling since Oct 2024): the Data API does not flag
  Shorts. Refined in a later phase; it feeds the separate Shorts baseline. A long-form video
  under 3 minutes is a false positive; the accurate fix is probing `youtube.com/shorts/<id>`
  (200 = Short, redirect to `/watch` = not), at one HTTP request per video.
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
