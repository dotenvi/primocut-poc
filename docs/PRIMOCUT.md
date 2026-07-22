# PRIMOCUT

**Pri**nt **Mo**ney with **Cut** — automation of YouTube clip channels.

> This is the **product** source of truth (the what and why) and it **evolves in place**: new
> phases update this file instead of adding new markdown documents to `docs/`. For how it is
> built and run — roadmap detail, current state, run guide, architecture, resilience and the
> decision log — see [`DEVELOPMENT.md`](./DEVELOPMENT.md).

## Vision

PRIMOCUT attacks the two most expensive problems of running YouTube clip channels:

1. **Finding what to clip in your own material.** The application receives the URL of a YouTube live (podcast, stream) **within a project** (see below), downloads the audio and the chat replay, transcribes it with timestamps and, on demand, uses AI to generate an index of the topics that were discussed. The user reads the report — instead of watching 3-4 hours of video — and identifies the passages they want to turn into clips. A live exists to become clips for a niche, so it is **contained in exactly one project** (1 live → 1 project → N own channels).
2. **Knowing what works in the niche.** Competitor channels, grouped into projects, are continuously monitored through public data. The system surfaces outlier videos, packaging patterns (title/thumbnail), posting strategy and audience reaction, so editorial decisions are driven by evidence instead of intuition.

A **project is a NICHE** — not a single channel. One niche holds several of the user's **own channels** (where the clips get posted), the **competitor channels** watched for that niche, and the **lives** submitted for cutting. Containment, not filtering: a gaming live never belongs to a politics niche.

## Live report (Phases 0–1 — delivered)

From a URL, a navigable report containing:

- Full searchable transcript with clickable timestamps and an embedded YouTube player — clicking a topic or a "Watch" action seeks the player to the right moment (external `&t=SECONDS` links remain as fallback).
- Chart of chat activity throughout the live (message peaks = clip candidates), with 🔥 markers on topics that coincide with peaks.
- Macro-topics in chronological order (**opt-in — costs LLM**): title, short summary, start/end timestamp, clip-potential score. Topic count is unbounded: one topic per genuinely distinct subject.
- CLI equivalent (`primocut run`) producing the same report as Markdown + JSON.

## Competitor channel modeling (Phase 2 — delivered)

Given competitor channels supplied by the user (no niche-wide discovery), grouped into **projects**:

- **Projects (niches)**: a project is a niche. It owns its **lives** and its **own channels** (direct 1:N — the clip destinations), while **competitor** channels are global entities linked to projects via M:N. The same competitor channel in two projects is collected once; per-channel metrics are computed once; niche-level reports (rankings, pattern distributions) are scoped per project so one niche never contaminates another. Detaching a competitor channel keeps its history; orphaned channels stop being collected until re-linked. Own channels are the user's own channels inside the niche and are not to be confused with competitor channels; they receive no snapshots in this phase.
- **Continuous collection** (YouTube Data API v3): backfill of the last 120 videos per new channel, which also records that channel's first snapshot so the dashboards are populated right away; snapshots daily for all videos and every 6h for videos younger than 7 days (views, likes, comment counts, channel stats); packaging-change detection by comparing title strings and thumbnail **content hashes** between snapshots; quota accounting on every cycle.
- **Metrics engine** (deterministic, no LLM cost): outlier classification into bands (relevant/strong/exceptional) over a rolling window, with separate rulers for Shorts vs long-form and provisional scores under 7 days of age. The **default basis is percentile** — the band cutoffs are the channel's own p90/p97/p99 (nearest-rank), so ~10% of a channel lands in the tail; the readable **median multiple** ("20.6× the median") is still shown but no longer classifies. Fixed multiples (3x/6x/10x) are a demoted **legacy basis**: on the skewed YouTube view distribution 3× the median lands around the channel's p72, marking ~30% of videos as outliers and — because that population then contaminated the pattern denominator — flattening every lift toward 1.0. Also: views-per-hour from snapshot deltas; resurgence alerts; packaging-change before/after impact; engagement rates; cadence; per-project conditional distributions of title/thumbnail attributes comparing outliers against a censored **control** group (band-less, mature, sufficient-baseline; provisional videos excluded from both), gated by **non-overlap of 95% Wilson intervals** in either direction (positive pattern or anti-pattern).
- **AI enrichment (opt-in — costs LLM)**: structured thumbnail attributes via vision model and title semantics via cheap model, cached by content hash and shared across projects. The batch target is **kind-aware**: `title`/`thumbnail` feed the two-group pattern comparison, so they cover **every mature video** — both the banded outliers *and* the band-less control (provisional/insufficient-sample stay out). Enriching only the outliers, as the earlier design did, left the control side empty and every Wilson comparison with nothing to separate, so Patterns (and the Cut Room Packaging kit it feeds) stayed blank. The trigger reports **how many videos are new vs. already cached** before running and dispatches only the cache misses (an unchanged content hash never recharges). **Comments** target only the definitive outliers (a synthesis summarises one group, it does not compare two); collection is automatic (top-liked comments fetched when a video is a definitive outlier, stored raw, quota-accounted); only the LLM synthesis pass is triggered manually — recurring themes, overall sentiment and the audience's **explicit requests** (guests, subjects, formats), which the project dashboard aggregates into a ranked demand list for the niche. Every quote it shows is a real comment: the model selects comment indices, never text.
- Private YouTube Analytics data (CTR, retention, impressions) is unavailable for third-party channels and is not modeled.

## Assisted cutting (Phase 3 — delivered)

Phases 0–2 tell the user **what** to clip; Phase 3 is where they **decide and compose** it, inside the Cut room, without leaving the report. **All steps are delivered**: the decision bench + virtual preview, per-item rendering (with surgical WhisperX refinement), the deliverable export bundle, and the AI packaging (titles + thumbnail brief + candidate frames) with the audience-asked signal — each opt-in and driven by the niche's Phase 2 patterns.

- **Decision bench.** The user triages a live's topics over days, in three columns: a **Stage** (the embedded player), the **topic bench** (each topic a decision line, cycling `open → in → maybe → discarded → open`), and the **Composition** of the active cut. Two concepts are kept deliberately separate (they were one field before — see the Data model note): **topic triage** is the user's opinion *about a topic*, scoped to the live and shared across cuts; **cut membership** (`in`) is belonging *to a specific cut*, and is never stored as triage. A **Cut** is an **ordered selection of N items**, each item a topic **or** a manual time range; a live has N cuts (one live → several cuts), and one is active at a time.
- **Virtual preview.** The player has a **Source | Preview cut** toggle. Preview plays *only* the active cut's items, in order — pure seek, **no download and no encode**. A segmented scrub bar (one segment per item, width ∝ duration) drives it. The seek gap between items is presented as UI (a *"item N →"* overlay while YouTube buffers), with the honest note that the **rendered** clip is seamless.
- **Signals that help decide.** A topic that coincides with an already-detected **chat peak** carries a `chat N×` tag (free, existing data). An **audience-asked** tag (delivered) — matching a niche's synthesised audience requests to a topic via one **cheap LLM pass, cached per live** — fills the slot the bench reserved: it is **opt-in**, degrades silently to nothing when the niche has no comment syntheses, and clamps every match back to a real topic id (never invents one).
- **Rendering (delivered).** Each cut item renders to its own MP4 (**H.264 + AAC, original resolution**), downloaded by **section** (yt-dlp `--download-sections`, 15 s keyframe-safety margin, an ephemeral intermediate) and cut with FFmpeg — **NVENC re-encode by default** for exact boundaries, a `libx264` fallback, and an opt-in `--fast-copy`. It runs in the **background** on the existing queue while the user keeps deciding, with **per-item degradation** (a failed clip never blocks the others) and a **pay-once cache** by parameters. Clips are **deliverables**: stored under `PRIMOCUT_CLIPS_DIR/<video_id>/<cut_id>/NNN-slug.mp4`, outside any automatic cleanup, deleted only explicitly. **Surgical WhisperX** word-accurate refinement is **delivered**: when an align venv is configured, an `aligning` step snaps each item's `[start, end]` to the nearest word edge within **±3 s** (never mid-word) using wav2vec2 (PT-BR) on **only the item's window from the already-downloaded section** — never re-transcribing the live — under the GPU lock, with a **silent fallback** to the original bounds. WhisperX lives in a **separate venv** (it conflicts with the validated faster-whisper worker); diarization is a documented, deferred extension point.
- **Export (delivered).** An **Export** action packages a whole cut into a **deliverable bundle** — a background job producing a **timestamped** folder (history kept, never auto-swept) with the clips **copied** in composition order (`NNN-slug.mp4`, originals under `PRIMOCUT_CLIPS_DIR` untouched), a per-clip **`NNN-slug.srt`** offset to the clip's effective (WhisperX-refined) start, a **`master.mp4`** (concat-demuxer **stream copy** by default — no re-encode, since every clip shares the render profile; `--skip-master` opts out), and **`manifest.md`/`manifest.json`** carrying the live/cut metadata and each item's final timestamps/title with **packaging fields present but empty** until AI packaging fills them. **`--upscale-1440p`** re-encodes **only the master** to 1440p via NVENC (YouTube assigns better codecs to ≥1440p uploads); the clips stay original resolution. An **un-rendered item fails** the export with a clear list unless **`--render-missing`** renders them first. The UI downloads a **zip**; the CLI (`primocut export`) prints the folder path.
- **AI packaging (delivered).** Per clip (a clip is a video of its own), driven by the LIVE's project Phase 2 patterns read under **three lenses** — **differentials** (the Wilson-gated conditional distributions that separate outliers), **conventions** (booleans near-universal across the whole niche — a requirement, not a lever), and **carries-value** (recurring values like *the name that carries*, guarded by a min-videos + min-lift floor) — plus **real outlier examples** (titles/attributes cited by video id). Each pass is **opt-in** (guideline 9): **K titles** (default 5, selectable) where every proposal cites which measured patterns it applies (a citation that resolves to no measured pattern is dropped — never invented); a **structured thumbnail brief** (composition, text, colours, graphics + references to the outliers that used the pattern — **no image generation**); and **candidate frames** (default 3, selectable) extracted **100 % by FFmpeg** (scene detection + sampling, **no LLM**), with a **separate, explicit** opt-in vision pass that ranks them against the brief. Results are **paid once** per clip (content hash over the clip + the niche patterns), regenerated only on request, and flow into the export manifest. **Degradation, never invention**: a niche with insufficient patterns generates in a generic mode with a warning; a **flat** niche (like *Cortes do MBL*) yields an honest brief that says packaging is not the lever and leans on the subject/carries-value. In Step 1 the **Packaging kit** panel was read-only reference; it now carries the generation buttons.
- **CapCut hand-off.** The export bundle is **CapCut-ready** by design — standard `NNN-slug.mp4` clips, a `master.mp4`, per-clip `.srt`, candidate-frame stills and the `manifest.md`/`manifest.json` carrying the generated titles/brief. There is no dedicated CapCut integration (nor one needed): the deliverables are imported into CapCut directly.

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
           packaging changes, attributes, comments, enrichments,
           events (append-only Radar log)
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

- **Video** (a submitted live): id, **project_id** (the niche it is contained in — required), url, title, channel, duration, status (queued/downloading/transcribing/analyzing/done/error)
- **Transcript**: video_id, segments [{start, end, text}], language, model used
- **ChatSignal**: video_id, time series [{minute, msgs, laughs, emotes}], detected peaks [{ts, intensity}]
- **Topic**: video_id, title, summary, ts_start, ts_end, score
- **Job**: video_id, stage, progress, error

Competitor modeling:

- **Project** (a niche): id, name, description, active — contains its lives (1:N), own channels (1:N) and, via M:N, the competitor channels watched for the niche
- **OwnChannel** (the user's own channel inside the niche — clip destination): id, project_id, channel_url, handle, title, role (nullable: 'main' | 'shorts' | free), added_at. Unique (project_id, channel_url). Distinct from CompetitorChannel; receives no snapshots in this phase
- **CompetitorChannel** (global): channel_id, handle, title, uploads_playlist_id, active — linked to projects via **ProjectChannel** (M:N)
- **ChannelSnapshot**: channel_id, captured_at, subscribers, total_views, video_count
- **CompetitorVideo**: channel_id, video_id, title, published_at, duration_s, is_short, thumbnail_hash, comments_state
- **VideoSnapshot**: video_id, captured_at, views, likes, comments
- **PackagingChange**: video_id, field (title|thumbnail), old/new value, detected_at, vph_before/vph_after
- **VideoAttributes**: video_id, source (syntactic|title_llm|thumbnail_llm), attributes JSON, content_hash
- **VideoComments**: video_id, comment_id, text, like_count, author, fetched_at (top comments of outliers, collected automatically)
- **VideoEnrichment**: video_id, kind (comment_synthesis), payload JSON, content_hash — kept apart from VideoAttributes so a narrative synthesis never counts as a packaging pattern
- **Event** (append-only): id, category (niche|ops), type, severity (info|error), project_id, channel_id, video_id, live_id, occurred_at, payload JSON, seen_at, resolved_at — index (category, occurred_at DESC). The historical fact log behind the Radar home (below). CompetitorVideo also gains **last_band_emitted** for the emission guard.

Outlier scores and VPH are computed on read (over snapshots), never stored — but the *moment* a threshold was crossed is a fact that a recompute would erase, so it is recorded once, when it happens, in **Event**.

Assisted cutting (Phase 3):

- **TopicTriage** (the user's opinion about a topic, scoped to a live): video_id, topic_id, state ∈ `maybe | discarded`, updated_at. The **absence** of a row means `open`. It persists across sessions and is **shared across the live's cuts** — it is *not* membership of any cut. `in` is never written here.
- **Cut** (an ordered composition): id, video_id, name, status, created_at. A live has **N cuts**; each live auto-creates a default one (`Cut 1`).
- **CutItem** (an ordered slot of a cut): cut_id, position, topic_id (**nullable**), start_s, end_s. An item is a **topic** (topic_id set, start/end snapshotted from it) **or** a **manual range** (topic_id null). Membership (`in`) is derived exclusively from these rows.
- **Derived topic state** on the bench is computed, in this precedence: a CutItem in the **active** cut → `in`; else TopicTriage `maybe` → `maybe`; else TopicTriage `discarded` → `discarded`; else `open`. So a topic can be `in` cut A while globally `maybe` (visible in cut B).
- **Clip** (a rendered deliverable, one per CutItem): cut_item_id (unique, cascades with the item), video_id, cut_id, position, filename/rel_path, state (`queued | downloading_section | aligning | cutting | done | error` — `aligning` only when an align venv is configured), encode_profile/encoder_used, params_hash (pay-once cache over range + padding + encode profile + WhisperX tolerance), start_s/end_s, size_bytes. The MP4 is stored under `PRIMOCUT_CLIPS_DIR` and is outside automatic cleanup — deleted only explicitly.
- **CutPackaging** (the opt-in AI packaging of one clip): cut_item_id + kind (`title | thumb_brief | frame_ranking`, unique per pair, cascades with the item), cut_id, payload JSON, content_hash (pay-once over the clip content + the niche patterns), state/error. `title`/`thumb_brief` are LLM passes; `frame_ranking` is the opt-in vision pass over the deterministically extracted frames (the frame JPEGs live on disk under the clip's frames dir — no row). Regenerated only on explicit request.
- **LiveAudienceMatch** (the cached audience→topic match, one per live): video_id (PK, cascades with the live), payload JSON (topic_id → the requests it delivers), content_hash (folds the live's topics + the niche's request set), state/error. Fills the bench's `audience_asked` slot; degrades silently to empty when the niche has no comment syntheses.

### Radar (events — delivered)

An outlier score does not survive a recompute: "crossed p90 yesterday at 14h" would vanish the moment the video drops a band. The **Event** table is the append-only record of those facts, read by the Radar feed (a daily "what happened since yesterday").

- **Emission rule (`outlier_crossed`, anti-flapping)**: a crossing is emitted **only on a transition to a strictly higher band** than the one last emitted (`competitor_videos.last_band_emitted`). A video climbing p90→p97→p99 emits three events, one per rung; a video oscillating around the p90 cut emits exactly one. A provisional (< 7-day) crossing is emitted with `provisional=true`; because the guard tracks the highest band emitted, it re-emits only on a genuine step up as it matures. This runs in the snapshot cycle and is the **single** crossing-detection point.
- **One source of truth**: automatic comment collection is a **consumer** of `outlier_crossed` (it no longer re-detects), removing the old risk of collecting twice or never.
- **Niche vs ops**: niche events are channel facts written once with `project_id` NULL, attributed to projects **on read** by joining `project_channels` — a channel in two projects yields one row rendered with two badges, never a duplicate. Ops events are project-scoped failures (severity=error) that clear via `resolved_at` and are never marked "seen" (a failure does not resolve by being looked at); niche events carry a dismissible `seen_at`.
- **Endpoints**: `GET /api/events?category=niche&project_id=&since=&limit=` (feed with badges), `GET /api/alerts` (open ops), `GET /api/activity` (full paginated log + quota accounting), `POST /api/events/seen` (mark a set of ids or everything up to a timestamp).
- Producers wired today: `outlier_crossed`, `packaging_changed`, the per-cycle `cycle_done` (quota accounting), and the ops event `pipeline_failed` (severity=error, carrying `project_id` + `live_id`, emitted when a live's pipeline fails). Planned producers: `resurgence`, `packaging_verdict`, `backfill_done`, and the remaining ops events (`comments_disabled`, `quota_exhausted`).

## Interface and navigation

The information architecture follows the one principle that shapes the product:
**events are global, patterns are per project.** Channel metrics do not depend on
any project, so the **Radar** (the cross-project event feed) and **Activity** (the
system log) sit *above* the projects; niche analysis is scoped to a project, so
everything else lives *inside* one. The project is the container — it is a route,
not a dropdown buried in a menu. Every destination is a linkable URL and the
browser's back/forward buttons work (hash routing; no hidden project state).

| Route | Screen |
|---|---|
| `/` | **Radar** — a "Needs you" band of open failures (with a direct Requeue), then the niche event feed with project badges, per-project filter and *Mark all as seen*. Success is silent (a finished pipeline never enters the feed); failure is loud. |
| `/activity` | The full system log + quota accounting. |
| `/p/{pid}/lives` | The project's lives. **+ New live** is a button here (not a menu item). |
| `/p/{pid}/lives/{lid}` | **Cut room** — the decision bench: a **Stage** (player with a Source \| Preview cut toggle), the **topic bench** (each topic a decision line cycling open/in/maybe/discarded, with chat-peak signals), and **Composition + actions** (the active cut's ordered items, plus the read-only **Packaging kit** fed by the niche's patterns). |
| `/p/{pid}/niche` | **Niche** — tabs *Patterns \| Outliers \| Channels*. |
| `/p/{pid}/niche/channels/{cid}` | A tracked channel's dashboard (a drill-down of the Channels tab, no longer a top-level destination). |
| `/p/{pid}/niche/channels/{cid}/videos/{vid}` | A competitor video's detail, with the channel-spread strip. |
| `/p/{pid}/settings` | The project's own channels + the outlier bands (read-only — bands are global config). |

Vocabulary, consistent across the UI (English): **Niche** (was "Competitors"),
**Cut room** (a live's page), **Radar** and **Activity** (the global feeds). The
"channel spread" strip — a per-channel/-format view histogram with this video's bar
highlighted — is one component shared by the Radar cards and the video detail, both
reading `GET /api/channels/{cid}/distribution?format=` (single) or its
`?channel_ids=` batch. Opt-in AI enrichment uses a deliberate teal `btn-ai` style;
red is reserved for failure. The web UI is built with Tailwind CSS (Manrope + DM
Mono), tokens ported from the approved navigation prototype.

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
- **Phase 3 — Assisted cutting ✅:** the Cut room is a decision bench — triage a live's topics into persistent states, compose ordered cuts, and preview them virtually in the embedded player (no encode). Then, per item: rendering with FFmpeg/NVENC + a surgical WhisperX pass for word-accurate boundaries, a deliverable export bundle (clips + SRT + concat master + manifests, CapCut-ready), and AI packaging (K titles + thumbnail brief + deterministic candidate frames, with an opt-in vision ranking) from the Phase 2 niche patterns — plus the opt-in, cached audience-asked signal. See the [Assisted cutting](#assisted-cutting-phase-3--delivered) section above.
- **Phase 4 — Publishing:** AI titles/descriptions, vertical format, upload via the YouTube API, multi-user.
