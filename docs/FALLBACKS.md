# PRIMOCUT — Fallbacks and Resilience Catalog

A map of **every** recovery/degradation behavior in the system. Product source of truth:
[`PRIMOCUT.md`](./PRIMOCUT.md). Per-stage resilience plan: `approved plan` (see
[`FASE0-STATUS-E-EXECUCAO.md`](./FASE0-STATUS-E-EXECUCAO.md)).

## Principles

- **Degrade, don't abort.** Whenever possible, produce the best report from what succeeded,
  flagging the unavailable sections. Only abort when it is **impossible to continue**.
- **Essential vs. degradable.** Audio and transcription are essential (without them there is nothing
  to analyze); metadata, chat and analysis are degradable.
- **Pay-once cache.** Expensive stages are persisted in `data/<video_id>.*` and reused;
  no work already completed (and paid for) is redone.
- **Never invent timestamps.** Analysis robustness never relaxes the rule: the LLM selects segment
  indices; the code resolves them into seconds and clamps invalid indices.

### Status legend
- ✅ **Implemented** (in the code today) — all fallbacks listed below are already implemented and covered by tests.

---

## 1. Fallbacks per pipeline stage

| Stage | Trigger | Fallback behavior | Status |
|---|---|---|---|
| **Metadata** | `yt-dlp -J` fails on a transient error (network/rate limit) | Retry with exponential backoff | ✅ |
| **Metadata** | `yt-dlp -J` fails permanently | **Degrades**: builds a minimal `Video` (id extracted from the URL, title/channel "(unknown)", duration 0); adds a warning; duration is filled in later from `transcript.duration` | ✅ |
| **Audio download** | Transient yt-dlp error | Retry with backoff | ✅ |
| **Audio download** | YouTube extractor broke for a client | Tries `--extractor-args youtube:player_client=<c>` over a configurable list (e.g. `android,web`) | ✅ |
| **Audio download** | Final failure (essential) | **Aborts** with a clear message (without audio there is no transcription) | ✅ |
| **Transcription** | GPU/VRAM error (does it fit in 8 GB?) | Loop degrading the `compute_type`: `float16 → int8_float16 → int8`; records a warning when it degrades | ✅ |
| **Transcription** | No `compute_type` fit (essential) | **Aborts** with guidance (no CPU fallback, by decision) | ✅ |
| **Transcription** | `libcublas`/`libcudnn` not on `LD_LIBRARY_PATH` | Preloads the CUDA libs from the NVIDIA pip packages (`nvidia-cublas-cu12`/`nvidia-cudnn-cu12`); if absent, lets ctranslate2 try the system PATH | ✅ |
| **Chat** | `chat-downloader` raises / no replay | **yt-dlp `--live-chat` fallback** (parses the message offsets) | ✅ |
| **Chat** | Both fail / replay does not exist | Returns `None`; the pipeline **continues without chat** (optional signal) and records a warning | ✅ |
| **Analysis (topics)** | Transient provider error per window (429/5xx/timeout) | Retry with backoff on the same model (without switching model/provider) | ✅ |
| **Analysis (topics)** | Permanent failure on a window (credit, auth, no tool use) | **Ends the stage keeping whatever was already collected** (partial); the pipeline degrades the report + warning | ✅ |
| **Analysis (topics)** | LLM response off-format | Tolerant parsing: normalizes an indexed dict, **ignores malformed items with a warning** (never tears down the stage) | ✅ |
| **Analysis (topics)** | LLM returns out-of-range indices | `resolve_span` **clamps** to the real limits and sorts them if reversed | ✅ |
| **Report** | Some section has no data | Renders "No topic identified" and/or "No chat replay"; never fails | ✅ |
| **Report** | The run had degradations | A **"⚠️ Degraded run"** section right after the metadata, listing what failed/was skipped | ✅ |

---

## 2. Cache / resume (never re-pays for what already succeeded) ✅

Each file in `data/<video_id>.*` is reused across re-runs:

| File | Stage it avoids repeating |
|---|---|
| `*.16k.wav` | Audio download + normalization |
| `*.transcript.json` | Transcription (the most expensive GPU stage) |
| `*.topics.json` | Macro analysis (LLM, paid) |

- ✅ **Implemented tweak:** the **partial** result of a stage (when it is ended mid-way by a
  permanent failure) feeds the current report, but is **not** cached — so a re-run tries again.
  Because the windows are few (~2 in a 33-min live), the retry is cheap.
- To force a recompute, delete the corresponding cache file in `data/`.

---

## 3. Cross-cutting robustness guards ✅

Behaviors that prevent failures before they become errors (already in the code):

| Guard | What it does |
|---|---|
| **URL validation** (`validate_youtube_url`) | Rejects out-of-format links (non-YouTube host, invalid ID, escape backslashes) with a clear message and **does not trigger** the pipeline; returns the canonical `watch?v=<ID>` URL |
| **ID extraction** (`extract_video_id`) | Accepts `watch?v=`, `youtu.be/`, `/live/`, `/embed/`, `/shorts/` and falls back to the last path segment |
| **yt-dlp via module** | Invokes `python -m yt_dlp` through the current interpreter → works even without `.venv/bin` on `PATH` |
| **doctor: yt-dlp** | Considers it OK if the `yt_dlp` **module** is installed (does not require the binary on PATH) |
| **doctor: API key** | Validates the key of the **selected provider** (`llm_provider`) |
| **doctor: provider SDK** | Warns if the provider SDK (openai/anthropic) is not installed |
| **OpenAI SDK import** | If the `openai` package is missing (used by OpenAI/OpenRouter), a clear error stating the install command |
| **No tool support** (`ToolCallError`) | If the model does not return the forced function/tool call, a clear error (probably no tool-use support) + a hint about the `primocut models` command |
| **Chat signals** | `z-score` with standard deviation 0 → no peaks (no division by zero); empty message list → empty signal |
| **`primocut models`** | Lists OpenRouter models that support tools (including **free** ones) to pick a compatible one without spending |

---

## 4. LLM provider selection ✅

The analysis is provider-agnostic (`SelectionLLM` interface). Automatic selection via `PRIMOCUT_LLM_PROVIDER`:

| Provider | SDK | Note |
|---|---|---|
| `anthropic` (default) | `anthropic` | Default model `claude-sonnet-5` |
| `openai` | `openai` | Default model `gpt-4o` |
| `openrouter` | `openai` (custom base_url) | Default model `openai/gpt-4o-mini`; `provider/model` slugs |

> **There is no automatic provider/model switch on failure** (decision: LLM = "retry only").
> Switching provider/model is always explicit, via `.env` or the `--provider`/`--model` flags.

---

## 5. Matrix: essential vs. degradable

| Stage | If it fails for good | Does the report come out? |
|---|---|---|
| Metadata | Degrades (minimal Video) | ✅ Yes, with a warning |
| Audio download | Aborts | ❌ No (essential) |
| Transcription | Aborts | ❌ No (essential) |
| Chat | Continues without chat | ✅ Yes, with a warning |
| Analysis (topics) | Uses the partial (or empty) result | ✅ Yes, with a warning |
| Report | — | ✅ Always generated |

---

## 6. Related environment variables

| Variable | Effect | Status |
|---|---|---|
| `PRIMOCUT_LLM_PROVIDER` | `anthropic` \| `openai` \| `openrouter` | ✅ |
| `PRIMOCUT_ANTHROPIC_MODEL` / `PRIMOCUT_OPENAI_MODEL` / `PRIMOCUT_OPENROUTER_MODEL` | Model per provider | ✅ |
| `PRIMOCUT_WHISPER_COMPUTE_TYPE` | Force the `compute_type` (e.g. `int8_float16` to fit in VRAM) | ✅ |
| `PRIMOCUT_WHISPER_DEVICE` | `cuda` (default) \| `cpu` | ✅ |
| `PRIMOCUT_CHAT_ZSCORE_THRESHOLD` | Chat peak detection threshold | ✅ |
| `PRIMOCUT_WINDOW_TARGET_SECONDS` / `PRIMOCUT_WINDOW_OVERLAP_SECONDS` | Analysis windowing | ✅ |
| `PRIMOCUT_RETRY_ATTEMPTS` / `PRIMOCUT_RETRY_BASE_DELAY` / `PRIMOCUT_RETRY_MAX_DELAY` | Retry (backoff) policy for the stages | ✅ |
| `PRIMOCUT_WHISPER_COMPUTE_FALLBACKS` | GPU `compute_type` degradation sequence | ✅ |
| `PRIMOCUT_YTDLP_PLAYER_CLIENTS` | List of `player_client` values to try on download | ✅ |

---

## 7. Code pointers

- Pipeline / orchestration: `src/primocut/pipeline.py`
- Analysis (tolerant parsing, providers, tool use): `src/primocut/analysis/{parse,llm,topics}.py`
- Transcription (CUDA preload, engine): `src/primocut/transcription/faster_whisper_engine.py`
- Download (URL, yt-dlp, ffmpeg): `src/primocut/download/audio.py`
- Chat (optional): `src/primocut/chat/downloader.py`
- Models (resolve_span, clamp): `src/primocut/models.py`
- Diagnostics: `src/primocut/doctor.py`
- (✅) Cross-cutting resilience (retry/backoff/classification): `src/primocut/resilience.py`
