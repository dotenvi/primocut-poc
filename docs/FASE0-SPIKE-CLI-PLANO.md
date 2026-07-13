# PRIMOCUT — Phase 0 (CLI spike) — Implementation Plan

> Planning document for Phase 0 described in the [`PRIMOCUT.md`](./PRIMOCUT.md) roadmap.
> The project source of truth is still `PRIMOCUT.md`; this file details **how**
> Phase 0 (the CLI spike) was implemented.

> **Historical note (pre-Phase 2):** this document describes Phase 0 as originally planned, which
> included a second, fine-grained "cut moments" analysis pass and a 10-20 topic cap. Both were
> later **removed**: the analysis now has a single macro-topics pass with no numeric cap. The
> `analysis/moments.py` module and the `CutMoment` model no longer exist. Read the moment/cap
> mentions below as history.

## Context

Phase 0 is a **command-line spike**: given the URL of a YouTube live, produce a Markdown report
with the topics discussed, following the pipeline
**audio → chat replay → transcription → LLM analysis**.

Spike goals (from the roadmap): validate the **GPU on WSL2**, compare the segmentation quality
(**macro topics** vs. **fine-grained moment detection**) and validate **chat peak detection**.

The code is **modular** from the start so it can evolve into Phase 1 (queue + database + web), with
clean boundaries between modules and a `TranscriptionEngine` abstraction.

### Environment recon (run on 2026-07-12)
- RTX 3070 Ti GPU (8 GB), driver 595.97, CUDA 13.2 — **visible** via `nvidia-smi`. ✅
- **Missing** on the host: `ffmpeg`, `yt-dlp`, and `ANTHROPIC_API_KEY` not set. → This is exactly
  what the `doctor` command must detect; we build and demonstrate `doctor` against this
  "unhealthy" environment.
- Python 3.13.5 available.

### Confirmed decisions
- **Native Python tooling** (no uv for now, per spec §Stack): `python -m venv .venv`
  + `pip` + `requirements.txt`. A minimal `pyproject.toml` (setuptools) only registers the
  `primocut` entry point via `pip install -e .`. (uv is deferred until authorized.)
- **Two independent LLM passes** (macro topics / fine-grained moments) — better comparison of the
  segmentation quality. *(Historical: the second pass was later removed.)*
- Default analysis model: **`claude-sonnet-5`** (overridable via env).
- `doctor` **reports + prints fix commands** (no auto-install).

---

## Directory structure

```
primocut/
├── pyproject.toml               # minimal setuptools: metadata + `primocut` entry point (pip install -e .)
├── requirements.txt             # runtime deps (pip)
├── requirements-dev.txt         # pytest, pytest-mock
├── README.md                    # how to run + test each module
├── .env.example                 # ANTHROPIC_API_KEY=...
├── .gitignore
├── src/primocut/
│   ├── __init__.py
│   ├── config.py                # Settings (pydantic-settings): paths, model, window, retention
│   ├── logging_conf.py          # structured logging + stage progress
│   ├── models.py                # pydantic models (Video, Transcript, Segment, ChatSignal, Topic, Report)
│   ├── cli.py                   # Typer app: `doctor`, `run`
│   ├── doctor.py                # environment diagnostics (CUDA/GPU, ffmpeg, yt-dlp, API key)
│   ├── pipeline.py              # orchestrates the stages, emits progress
│   ├── download/
│   │   ├── __init__.py
│   │   └── audio.py             # yt-dlp: metadata + audio; FFmpeg → 16kHz mono wav
│   ├── chat/
│   │   ├── __init__.py
│   │   ├── downloader.py        # chat-downloader (yt-dlp live_chat fallback); absence = None
│   │   └── signals.py           # msgs/min density, z-score peaks, laughs/emotes
│   ├── transcription/
│   │   ├── __init__.py
│   │   ├── engine.py            # TranscriptionEngine interface (ABC/Protocol)
│   │   └── faster_whisper_engine.py  # large-v3-turbo GPU impl.
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── windowing.py         # overlapping windows over segments
│   │   ├── llm.py               # Anthropic client + structured call (tool use / JSON)
│   │   ├── prompts.py           # topic prompt (segment selection, never invent ts)
│   │   └── topics.py            # macro pass + consolidation across windows
│   └── report/
│       ├── __init__.py
│       └── markdown.py          # renders the .md report
└── tests/
    ├── conftest.py              # fixtures: synthetic transcript, synthetic chat
    ├── fixtures/                # example transcript.json, chat.jsonl
    ├── test_models.py
    ├── test_doctor.py
    ├── test_chat_signals.py
    ├── test_windowing.py
    ├── test_analysis_selection.py   # ensures ts come from segments (never invented)
    ├── test_report_markdown.py
    └── test_transcription_engine.py # with a fake engine, validates the interface
```

**Timestamp anti-invention principle** (technical guideline #2): the LLM receives **indexed**
segments (`[i] mm:ss text`) and returns **segment indices** (`start_index`/`end_index`),
not time numbers. The code resolves the indices → seconds from the transcript. This makes it
*impossible* for the LLM to invent timestamps.

---

## Data models (pydantic, aligned with the spec §"Data model")

- `Segment{index:int, start:float, end:float, text:str}`
- `Transcript{video_id, language, model, segments:list[Segment]}` + helpers `duration`, `window()`
- `Video{id, url, title, channel, duration, status}` (status enum from the spec)
- `ChatMessage{offset_seconds:float, author, text}` and
  `ChatSignal{video_id, per_minute:list[MinuteBucket{minute, msgs, laughs, emotes}], peaks:list[Peak{ts, intensity, z}]}`
- `Topic{title, summary, start, end, score, status}` (macro) — `start/end` always resolved from segments
- `Report{video, transcript_meta, chat_signal|None, topics}` → `youtube_url(ts)` method

`youtube_url(ts)` → `https://youtube.com/watch?v={id}&t={int(ts)}`.

---

## TranscriptionEngine interface (technical guideline #1)

```python
class TranscriptionEngine(Protocol):
    def transcribe(self, audio_path: Path, *, language: str = "pt") -> Transcript: ...
    @property
    def name(self) -> str: ...
```
- `FasterWhisperEngine`: `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`
  (fallback `int8_float16` if the 8 GB of VRAM get tight), `vad_filter=True`. Builds `Segment`
  with a sequential index.
- Phase 2 (WhisperX) plugs in here without touching the rest of the system.

---

## LLM analysis (technical guidelines #2 and #3)

1. **Windowing** (`windowing.py`): partitions `segments` into windows by target duration
   (default ~20 min of audio / ~8k tokens) with **overlap** (default ~60 s). Each window keeps the
   global segment indices.
2. **Macro pass** (`topics.py`): per window, the LLM selects segment ranges → topics; then
   **consolidates** across windows (deduplicates the ones crossing the overlap, merges neighbours).
   *(Historical: the original plan capped this at 10-20 topics and added a separate fine-grained
   "moments" pass in `moments.py`; both were later removed.)*
3. **LLM client** (`llm.py`): Anthropic SDK, **tool use / structured output** to force the schema
   (return indices). Default model `claude-sonnet-5` (via `config.py`, overridable via env).
   Key read from `ANTHROPIC_API_KEY`. In tests the client is **injectable/mocked** (no network).

---

## Chat (technical guideline #4) — optional signal

- `downloader.py`: tries `chat-downloader`; if there is no replay → returns `None` and the pipeline
  continues without it.
- `signals.py`: per-minute buckets (msgs, laughs via regex `k{4,}`/"haha", emotes), **z-score**
  over msgs/min → `peaks` (z ≥ threshold, default 2.0). Pure/deterministic → testable without network.
- In the report: a textual activity table/sparkline + peaks **cross-referenced** with topics
  (marks topics that coincide with a peak).

---

## Markdown report

`markdown.py` produces, in this order:
1. **Metadata**: title, channel, URL, duration, transcription model, date.
2. **Chat activity**: per-minute table + ASCII sparkline (blocks `▁▂▃▅▇`) + peak list;
   or a "no chat replay" notice when absent.
3. **Macro topics**: title · summary · [start–end] · score, with a **clickable link**
   `https://youtube.com/watch?v=ID&t=SECONDS`.

Topics that coincide with chat peaks get a marker (🔥).

---

## Implementation order (TDD, test-first, module by module)

Each step: write test(s) → implement → show the isolated test command.

0. **Scaffold**: `python -m venv .venv` → `pip install -r requirements.txt -r requirements-dev.txt`
   → `pip install -e .`. `pyproject.toml` (setuptools) only for metadata + the `primocut` entry point.
   logging, `config.py`.
1. **`doctor` FIRST** (`test_doctor.py` with mocked checks): validates GPU/CUDA, ffmpeg,
   yt-dlp, key. Real demo: `primocut doctor` (venv active) → shows the missing items + fix commands.
2. **`models.py`** (`test_models.py`): pydantic validation + `youtube_url`, `window()`.
3. **`chat/signals.py`** (`test_chat_signals.py`): z-score/peaks/laughs with synthetic chat.
4. **`transcription/engine.py`** + fake engine (`test_transcription_engine.py`): interface contract.
5. **`analysis/windowing.py`** (`test_windowing.py`): windows + overlap + global indices.
6. **`analysis` (topics/llm)** (`test_analysis_selection.py`): with a **fake LLM** that returns
   indices → proves timestamps come from segments (never invented) + consolidation.
7. **`report/markdown.py`** (`test_report_markdown.py`): snapshot of the `.md` with correct links.
8. **`download/audio.py`** + **`chat/downloader.py`**: thin yt-dlp/chat-downloader wrappers
   (light tests with mocked subprocess; real validation depends on ffmpeg/yt-dlp being installed).
9. **`pipeline.py`** + `run` in the CLI: orchestrates everything with progress; end-to-end.

### Dependencies (`requirements.txt`)
`typer`, `pydantic`, `pydantic-settings`, `anthropic`, `faster-whisper`, `chat-downloader`,
`yt-dlp`, `rich` (logging/progress), `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (CUDA on WSL).
`requirements-dev.txt`: `pytest`, `pytest-mock`.

---

## Verification (how to test)

Commands assume the venv is active (`source .venv/bin/activate`).
- **Per module (isolated, no network/GPU)**: `pytest tests/test_<mod>.py -v`. All the pure logic
  (signals, windowing, ts selection, report) runs without a GPU and without the API.
- **doctor (real)**: `primocut doctor` — should list missing ffmpeg/yt-dlp/key with the fix.
- **Transcription (real, GPU)**: after installing deps, transcribe a short clip and check `device=cuda`.
- **End-to-end**: `primocut run "<url>" -o report.md` with a short live; open `report.md`
  and click the timestamps.

## Out of scope (Phase 0)
Queue/Redis, SQLite, FastAPI, web UI, clip cutting/rendering, upload — deferred to Phase 1+.
