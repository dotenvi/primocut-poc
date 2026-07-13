# PRIMOCUT — Phase 0: Status and Execution Guide

> What was implemented in the CLI spike and the step-by-step to run it yourself.
> Source of truth: [`PRIMOCUT.md`](./PRIMOCUT.md). Plan: [`FASE0-SPIKE-CLI-PLANO.md`](./FASE0-SPIKE-CLI-PLANO.md).

> **Historical note (pre-Phase 2):** Phase 0 originally shipped two independent LLM passes (macro
> topics + fine-grained "cut moments"). The moments pass was later **removed** — the analysis now
> has a single, uncapped macro-topics pass. Mentions of moments / `*.moments.json` below are history.

## 1. Status — what was delivered

Modular Python project (already prepared for Phase 1), **58 tests passing** at the time, built with
TDD (test-first). All the MVP technical guidelines were met in Phase 0.

| Area | Module | What it does | Tests |
|---|---|---|---|
| Diagnostics | `doctor.py` + `cli.py` | Validates GPU/CUDA (WSL2), FFmpeg, yt-dlp and `ANTHROPIC_API_KEY`; prints the fix command for each missing item | `test_doctor.py` (10) |
| Models | `models.py` | pydantic models (`Video`, `Transcript`, `Segment`, `ChatSignal`, `Topic`, `Report`) + `resolve_span` and `youtube_url` | `test_models.py` (10) |
| Chat (optional) | `chat/signals.py` | msgs/min density, laughs/emotes, **z-score peaks** | `test_chat_signals.py` (7) |
| Chat (download) | `chat/downloader.py` | Replay via chat-downloader; absence returns `None` (does not block the pipeline) | `test_downloaders.py` |
| Transcription | `transcription/engine.py` + `faster_whisper_engine.py` | `TranscriptionEngine` interface (easy swap in Phase 2) + faster-whisper `large-v3-turbo` on GPU (lazy import) | `test_transcription_engine.py` (4) |
| Windowing | `analysis/windowing.py` | Splits long transcripts into overlapping windows, preserving global indices | `test_windowing.py` (5) |
| LLM analysis | `analysis/{llm,prompts,topics,consolidate}.py` | Macro-topics pass; consolidates across windows *(originally also a fine-grained moments pass, since removed)* | `test_analysis_selection.py` |
| Audio download | `download/audio.py` | yt-dlp + FFmpeg → 16kHz mono WAV | `test_downloaders.py` |
| Report | `report/markdown.py` | Metadata + chat activity (sparkline/table/peaks) + a topic list with `&t=SECONDS` links, 🔥 marker on peaks | `test_report_markdown.py` |
| Orchestration | `pipeline.py` | Chains the stages with progress; the `run` command | `test_pipeline.py` |

**Timestamp anti-invention guarantee (guideline #2):** the LLM receives indexed segments
(`[i] M:SS text`) and returns only `start_index`/`end_index`; the code resolves them into seconds
and **clamps out-of-range indices**. It is impossible for the LLM to invent a time.

## 2. Environment prerequisites

Already verified with `nvidia-smi`: **RTX 3070 Ti (8 GB) visible via CUDA on WSL2** ✅.
Missing (the `doctor` confirms): **FFmpeg**, **yt-dlp**, the **API key** and the heavy runtime libs.

> **Nuance of this environment:** the `python3.13-venv` package is missing (no `ensurepip`) and
> `sudo` asks for a password, so the venv was created with `--without-pip` and installs are done by
> pointing the system pip at the venv's interpreter. The commands below already use that form.

## 3. Setup (native Python tooling)

The venv (`.venv/`) and the light dependencies (typer, pydantic, anthropic, rich, pytest) are
already installed, and the `primocut` package is already registered (`pip install -e .`). To
recreate from scratch:

```bash
cd ~/projects/primocut

# venv without pip (because python3.13-venv is missing) + install via the SYSTEM pip.
# Do not activate the venv here: use /usr/bin/python3 (the venv's python has no pip).
python3 -m venv .venv --without-pip
/usr/bin/python3 -m pip --python .venv/bin/python install -r requirements-dev.txt
/usr/bin/python3 -m pip --python .venv/bin/python install --no-deps -e .
```

If you install the `python3.13-venv` package (`sudo apt install python3.13-venv`), the standard
flow applies: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && pip install -e .`.

## 4. Run the tests (per module, no GPU/network)

```bash
source .venv/bin/activate

pytest -q                                     # everything
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

## 5. Environment diagnostics (run this first)

```bash
source .venv/bin/activate
primocut doctor
```

Shows a table with GPU/FFmpeg/yt-dlp/API key and, for each failure, the fix command.
Exits with code `1` while there are pending items.

## 6. Enable the real pipeline

### 6.1. System and runtime dependencies

```bash
# FFmpeg (system) — needs sudo
sudo apt update && sudo apt install -y ffmpeg

# Heavy runtime libs (audio, transcription, chat) in the venv.
# IMPORTANT: use the SYSTEM pip (/usr/bin/python3). If you call just `python3`
# with the venv ACTIVATED, it uses the venv's python, which has no pip
# (error: "No module named pip"). The absolute path always works:
/usr/bin/python3 -m pip --python .venv/bin/python install faster-whisper yt-dlp chat-downloader

# (Optional) CUDA libs via pip, if faster-whisper cannot find cuBLAS/cuDNN on WSL:
/usr/bin/python3 -m pip --python .venv/bin/python install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

### 6.2. LLM provider and API key

The analysis is **provider-agnostic** (`SelectionLLM` interface). It supports **Anthropic**
(default), **OpenAI** and **OpenRouter**. Each provider bills its own API (a consumer subscription
— Claude Pro/Max or ChatGPT Plus — does **not** grant API access; you need balance/credits).

**Anthropic (default):**
```bash
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=sk-ant-...
```
Default model `claude-sonnet-5` (change it with `PRIMOCUT_ANTHROPIC_MODEL` or `-m`).

**OpenAI:**
```bash
/usr/bin/python3 -m pip --python .venv/bin/python install openai   # optional dep
# in .env:  OPENAI_API_KEY=sk-...  and  PRIMOCUT_LLM_PROVIDER=openai
```
Default model `gpt-4o` (change it with `PRIMOCUT_OPENAI_MODEL` or `-m`).

**OpenRouter** (one key, access to many models; OpenAI-API compatible):
```bash
/usr/bin/python3 -m pip --python .venv/bin/python install openai   # same SDK
# in .env:  OPENROUTER_API_KEY=sk-or-...  and  PRIMOCUT_LLM_PROVIDER=openrouter
```
Default model `openai/gpt-4o-mini` — use the OpenRouter *slugs* (`provider/model`), e.g.:
`anthropic/claude-3.5-sonnet`, `google/gemini-2.0-flash-001`, `meta-llama/llama-3.3-70b-instruct`.
Change it with `PRIMOCUT_OPENROUTER_MODEL` or `-m`.

You can also choose the provider/model directly on the command, without editing `.env`:
```bash
primocut run "<url>" -o report.md --provider openai --model gpt-4o-mini
primocut run "<url>" -o report.md --provider anthropic --model claude-haiku-4-5
primocut run "<url>" -o report.md --provider openrouter --model anthropic/claude-3.5-sonnet
```

`primocut doctor` validates the key of the **selected provider** (`PRIMOCUT_LLM_PROVIDER`).

> **The analysis requires tool use (function calling).** If the model does not support it, the
> pipeline fails with a clear message (`ToolCallError`) instead of producing an empty report.
> To find compatible OpenRouter models (including **free** ones, no balance needed):
> ```bash
> primocut models --free        # free + with tools
> primocut models               # all with tools
> ```
> Example free model with tools to try: `meta-llama/llama-3.3-70b-instruct:free`.
> (The `:free` models have daily request limits, but plenty to validate the pipeline.)

### 6.3. Confirm everything is ready

```bash
source .venv/bin/activate
primocut doctor        # everything should be OK / "Environment ready ✔"
```

### 6.4. Run end to end

```bash
# Start with a SHORT live/video to validate quickly (GPU, API cost, quality)
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md

# Open the report and click the timestamps
```

## 6.5. Cache / resume (never re-pays for what already succeeded)

Each expensive stage is persisted in `data/<video_id>.*` and reused across re-runs:
- `*.16k.wav` — normalized audio (does not re-download).
- `*.transcript.json` — transcription (does not re-transcribe).
- `*.topics.json` — macro topics (does not re-pay for the analysis).

Malformed items in the LLM response are **ignored with a warning** (they never tear down the whole
analysis). To force a recompute of a stage, delete the corresponding cache file in `data/`.

## 6.6. Resilience / fallbacks

Each stage has a recovery strategy and the pipeline **degrades instead of aborting** (produces the
best possible report, flagging what failed in a **"⚠️ Degraded run"** section). It only aborts when
it is impossible to continue (no audio or no transcription). Full catalog: [`FALLBACKS.md`](./FALLBACKS.md).
Related env vars: `PRIMOCUT_RETRY_*`, `PRIMOCUT_WHISPER_COMPUTE_FALLBACKS`, `PRIMOCUT_YTDLP_PLAYER_CLIENTS`.

## 7. First-run tips

- The first `run` downloads the `large-v3-turbo` model (a few GB) the first time — then it is cached.
- If the 8 GB of VRAM get tight, change the `compute_type`:
  `export PRIMOCUT_WHISPER_COMPUTE_TYPE=int8_float16`.
- Analysis window tuning (for 3-4h lives): `PRIMOCUT_WINDOW_TARGET_SECONDS`,
  `PRIMOCUT_WINDOW_OVERLAP_SECONDS`. Chat peak threshold: `PRIMOCUT_CHAT_ZSCORE_THRESHOLD`.
- If the download fails, update yt-dlp (it breaks frequently):
  `/usr/bin/python3 -m pip --python .venv/bin/python install -U yt-dlp`.
- Chat is optional: if the live has no replay, the report shows "No chat replay" and the rest of the
  pipeline continues normally.

## 8. What is left for later phases
Queue (RQ+Redis), database (SQLite), API (FastAPI), web UI, cut marking/export, audio cleanup and
automatic clip rendering (Phase 2) — none of that is part of Phase 0.
