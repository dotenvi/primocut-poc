# PRIMOCUT

**Pri**nt **Mo**ney with **Cut** — automation of YouTube clip channels.

## Vision

The application receives the URL of a YouTube live (podcast, stream), downloads the audio and the chat replay, transcribes it with timestamps and uses AI to generate an index of the topics that were discussed. The user reads the report — instead of watching 3-4 hours of video — and identifies the passages they want to turn into clips.

## MVP scope

Deliver, from a URL, a navigable report containing:

- Topics in chronological order: title, short summary, start/end timestamp, clip-potential score.
- Chart of chat activity throughout the live (message peaks = clip candidates).
- Full searchable transcript, with clickable timestamps (`https://youtube.com/watch?v=ID&t=SECONDS`).
- Marking topics as "cut" and exporting the timestamp list.

**Out of the MVP:** automatic clip cutting/rendering, title/thumbnail generation, upload to YouTube, vertical format, multi-user, authentication, remote deploy.

## Execution context

- Runs 100% locally: Debian via WSL2, single-user, no auth.
- GPU: NVIDIA RTX 3070 Ti (8 GB VRAM) via CUDA on WSL2. The driver lives on Windows; inside WSL use CUDA/cuDNN libraries via pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). Validate with `nvidia-smi`.
- PT-BR lives only (the distil Whisper models are English-only — do not use).

## Architecture

```
[Web UI] → [FastAPI] → [Queue (RQ + Redis)] → Workers:
                                              1. Audio download (yt-dlp, audio only)
                                              2. Chat replay download (chat-downloader)
                                              3. Transcription (faster-whisper, GPU)
                                              4. Analysis (LLM → topics + timestamps,
                                                 cross-referencing chat signals)
[SQLite] ← metadata, jobs, transcripts, topics, chat signals
[Local disk] ← audio files (temporary retention)
```

## Stack (decided)

| Layer | Technology |
|---|---|
| Audio download | yt-dlp (extract audio only, normalize with FFmpeg to 16kHz mono) |
| Chat download | chat-downloader (fallback: yt-dlp live_chat) |
| Transcription | faster-whisper, model `large-v3-turbo`, compute on GPU |
| Analysis/topics | LLM via API (Anthropic) |
| API | FastAPI |
| Queue | RQ + Redis |
| Database | SQLite |
| Frontend | Svelte (SPA compiled with Vite), served as static files by FastAPI |
| Dependencies | Standard Python: venv + pip + requirements.txt (no uv/poetry) |

## Data model

- **Video**: id, url, title, channel, duration, status (queued/downloading/transcribing/analyzing/done/error)
- **Transcript**: video_id, segments [{start, end, text}], language, model used
- **ChatSignal**: video_id, time series [{minute, msgs, laughs, emotes}], detected peaks [{ts, intensity}]
- **Topic**: video_id, title, summary, ts_start, ts_end, score, status (new/marked/discarded)
- **Job**: video_id, stage, progress, error

## Technical guidelines

1. **Project language is English**: every comment, docstring, identifier, log/error message and user-facing string is written in English, and so is the product documentation. Any new code follows the same rule.
2. **TranscriptionEngine as an interface**: the transcription worker must sit behind an abstraction so faster-whisper can be swapped for WhisperX (Phase 2) without touching the rest of the system.
3. **Timestamps are never invented**: the LLM analysis must reference exclusively timestamps that exist in the transcript. Structure the prompt/data so the LLM selects segments, rather than generating numbers.
4. **Long transcripts**: 3-4h lives exceed the LLM's useful context — process them in overlapping windows and consolidate the topics.
5. **Chat is an optional signal**: not every live preserves the chat replay; its absence must not block the pipeline. Peak detection by message density per minute (z-score) and reaction markers (laughs "KKKK", emotes).
6. **Retention**: audio is deleted by a cleanup job after N days (configurable, default 7); transcripts, topics and chat signals are permanent.
7. **Asynchronous jobs**: no heavy processing inside an HTTP request; everything goes through the queue with persisted status/progress.
8. **yt-dlp kept up to date**: it breaks frequently due to YouTube changes; pin an easy update process.

## Roadmap

- **Phase 0 — CLI spike:** command-line script: URL → audio → transcription → chat → topics in Markdown. Goal: validate GPU on WSL2, the quality of the macro-topic segmentation, and chat peak detection.
- **Phase 1 — MVP:** queue + database + web UI + navigable report + cut marking/export + audio cleanup.
- **Phase 2 — Assisted cutting:** automatic clip generation with FFmpeg; evaluate WhisperX (word-level timestamps + diarization).
- **Phase 3 — Publishing:** AI titles/descriptions, vertical format, upload via the YouTube API, multi-user.
