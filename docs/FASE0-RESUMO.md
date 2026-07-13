# PRIMOCUT — Phase 0 summary (CLI spike) — completed ✅

> Handoff for orchestration. Product source of truth: [`PRIMOCUT.md`](./PRIMOCUT.md).
> Execution guide: [`FASE0-STATUS-E-EXECUCAO.md`](./FASE0-STATUS-E-EXECUCAO.md).
> Resilience catalog: [`FALLBACKS.md`](./FALLBACKS.md). Plan: [`FASE0-SPIKE-CLI-PLANO.md`](./FASE0-SPIKE-CLI-PLANO.md).

**Goal:** validate, via the CLI, the pipeline live URL → audio → transcription → chat → topics in
Markdown; prove the GPU on WSL2, the segmentation quality (macro topics) and chat peak detection.

> **Note (pre-Phase 2):** Phase 0 originally explored a second, fine-grained "cut moments" pass in
> addition to the macro topics. That second pass was later **discontinued** — the analysis now has a
> single macro-topics pass, with no fixed cap on the number of topics.

## State
- **Completed.** The pipeline runs end to end; tested on a real ~33-min video (transcription of
  914 segments in ~82s on the RTX 3070 Ti). Test suite 100% green.

## Stack / environment
- Native Python (**venv + pip + requirements**, no uv — user decision). On WSL2 the venv was
  created `--without-pip` and installs via `/usr/bin/python3 -m pip --python .venv/bin/python`
  (the `python3.13-venv` package is missing).
- yt-dlp (audio) → FFmpeg (16kHz mono WAV) → **faster-whisper `large-v3-turbo` GPU** → LLM
  (tool use) → Markdown report.
- CUDA on WSL2: `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` libs via pip, preloaded into the process.

## Deliverables (modular architecture, designed for Phase 1)
- **CLI** (`typer`): `doctor` (GPU/FFmpeg/yt-dlp/key/SDK diagnostics), `run` (pipeline),
  `models` (lists OpenRouter models with tool use, including free ones).
- **Transcription** behind the `TranscriptionEngine` interface (easy swap for WhisperX in Phase 2).
- **LLM analysis = macro-topics pass**: segments the transcript into the subjects discussed.
- **Golden rule respected**: the LLM **never invents timestamps** — it selects *segment indices*
  (forced tool use); the code resolves them into seconds and **clamps** invalid indices.
- **Windowing** with overlap for long lives (3-4h) + consolidation across windows.
- **Optional chat**: peaks by z-score (msgs/min), laughs/emotes; a missing replay does not block.
- **Markdown report**: metadata, chat activity (sparkline+table+peaks), a topic list with
  **clickable `&t=SECONDS` links**, and a 🔥 marker on topics that coincide with peaks.
- **Models** in pydantic; **structured logging** with per-stage progress.

## Multi-provider LLM
- **Anthropic** (default, `claude-sonnet-5`), **OpenAI** (`gpt-4o`), **OpenRouter** (OpenAI-compatible;
  `provider/model` slugs). Selection via `PRIMOCUT_LLM_PROVIDER` or the `--provider/--model` flags.

## Resilience / fallbacks (implemented + tested)
- **Degrades instead of aborting**: produces the best possible report with a "⚠️ Degraded run"
  section; only aborts on an audio/transcription failure (the essential ones).
- **Pay-once cache** in `data/<id>.*` (audio, transcript, topics) — nothing already
  completed/paid for is redone.
- Retry+backoff (transient errors); metadata degrades to a minimal `Video`; **player_client
  fallback** in yt-dlp; **compute_type downgrade** on the GPU (float16→int8_float16→int8);
  **chat fallback via yt-dlp `--live-chat`**; analysis with per-window retry and **partial preserved**
  on a permanent failure; tolerant parsing of malformed LLM responses.

## How to run
```bash
source .venv/bin/activate
primocut doctor
primocut run "https://www.youtube.com/watch?v=<ID>" -o report.md
# optional: --provider openrouter --model meta-llama/llama-3.3-70b-instruct:free
```

## Out of scope / next steps (Phase 1)
Queue (RQ+Redis), database (SQLite), API (FastAPI), navigable web UI, cut marking/export,
audio cleanup. Technical candidates: incremental per-window cache; automatic provider switching
(today it is retry-only, by decision); WhisperX in Phase 2.
