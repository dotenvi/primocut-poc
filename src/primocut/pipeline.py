"""Pipeline orchestration, with per-stage progress.

Flow: metadata → audio → transcription → chat (optional) → analysis (macro
topics, opt-in via `create_topics`). `run_stages` is the core reused by CLI and
worker, parameterized by a
`Store` (cache/persistence) and a `ProgressReporter`. `run_pipeline` is the CLI
wrapper (Phase 0): uses `FileStore`+`LogReporter` and writes the Markdown report.
All resilience/degradation (see the Resilience section of docs/DEVELOPMENT.md) is preserved.
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .analysis.llm import SelectionLLM, get_llm
from .analysis.topics import extract_topics
from .chat import downloader as chat_downloader
from .chat.signals import compute_chat_signal
from .config import Settings, get_settings
from .download import audio as audio_dl
from .logging_conf import get_logger, setup_logging, step
from .models import ChatSignal, Report, Topic, Transcript, Video, VideoStatus
from .progress import LogReporter, ProgressReporter
from .report.markdown import render_report
from .store import FileStore, Store
from .transcription.engine import TranscriptionEngine
from .transcription.faster_whisper_engine import get_engine

logger = get_logger(__name__)


class PipelineError(RuntimeError):
    """Failure in an essential stage (audio/transcription) — aborts with a clear message."""


@dataclass
class PipelineResult:
    video: Video
    transcript: Transcript
    chat_signal: ChatSignal | None
    topics: list[Topic]
    warnings: list[str] = field(default_factory=list)


def run_stages(
    url: str,
    *,
    settings: Settings,
    store: Store,
    reporter: ProgressReporter,
    engine: TranscriptionEngine,
    llm: SelectionLLM,
    create_topics: bool = False,
    audio_dir: Path | str | None = None,
    metadata_fn: Callable = audio_dl.fetch_metadata,
    download_audio_fn: Callable = audio_dl.download_audio,
    download_chat_fn: Callable = chat_downloader.download_chat,
    gpu_lock=None,
) -> PipelineResult:
    """Pipeline core: reuses the Phase 0 stages, with caching via `store`.

    `gpu_lock` (context manager) serializes transcription on the GPU across
    workers. CLI/tests pass None → no lock (no-op).

    `audio_dir` is where the downloaded/normalized audio is written; when
    omitted it falls back to `settings.data_dir` (the worker/webapp path). The
    CLI passes a throwaway temp dir so nothing lands in `data/`.

    Topic extraction (the LLM step) is opt-in: it only runs when
    `create_topics` is True. Otherwise the run stops after chat and any
    previously cached topics are returned as-is.
    """
    warnings: list[str] = []
    audio_dir = Path(audio_dir) if audio_dir is not None else Path(settings.data_dir)

    # --- Metadata (degradable) ---
    reporter.update(stage="Metadata", progress=5)
    with step(logger, "Metadata"):
        try:
            video = metadata_fn(url)
        except Exception as exc:  # noqa: BLE001 — degrade to a minimal Video
            msg = f"Metadata unavailable ({type(exc).__name__}: {exc}); using minimal data."
            logger.warning(msg)
            warnings.append(msg)
            video = Video(
                id=audio_dl.extract_video_id(url), url=url,
                title="(unknown)", channel="(unknown)", duration=0.0,
            )
    store.save_video_meta(video)

    # --- Audio + Transcription (essential; store-backed cache) ---
    transcript = store.get_transcript(video.id)
    if transcript is None:
        try:
            reporter.update(stage="Audio download", progress=15,
                            video_status=VideoStatus.DOWNLOADING)
            with step(logger, "Audio download"):
                audio_path = download_audio_fn(url, audio_dir, video_id=video.id)
            reporter.update(stage="Transcription", progress=30,
                            video_status=VideoStatus.TRANSCRIBING)
            with step(logger, f"Transcription ({engine.name})"):
                if gpu_lock is not None:
                    logger.info("Waiting for the GPU to become free (lock)…")
                with gpu_lock or contextlib.nullcontext():
                    transcript = engine.transcribe(
                        audio_path, video_id=video.id, language=settings.whisper_language
                    )
            store.save_transcript(transcript)
        except Exception as exc:  # essential → abort with a clear message
            raise PipelineError(str(exc)) from exc
        warnings.extend(getattr(engine, "degradation_warnings", []))
    else:
        logger.info("Reusing cached transcript.")

    if video.duration <= 0:  # degraded metadata: fill from the transcript
        video.duration = transcript.duration
        store.save_video_meta(video)

    # --- Chat (optional) ---
    reporter.update(stage="Chat", progress=65)
    chat_signal = store.get_chat_signal(video.id)
    if chat_signal is None:
        with step(logger, "Chat (optional)"):
            messages = download_chat_fn(url)
            if messages:
                chat_signal = compute_chat_signal(
                    video.id, messages, threshold=settings.chat_zscore_threshold
                )
                store.save_chat_signal(chat_signal)

    # --- Analysis (opt-in; degradable): only runs the LLM when explicitly
    # requested (`create_topics`). Cache only writes on full success. ---
    topics = store.get_topics(video.id)
    if topics is None and create_topics:
        reporter.update(stage="Analysis: topics", progress=70, video_status=VideoStatus.ANALYZING)
        with step(logger, "Analysis: macro topics"):
            before = len(warnings)
            topics = extract_topics(transcript, llm, settings, warnings=warnings)
            if len(warnings) == before:
                store.save_topics(video.id, topics)
    elif topics is not None:
        logger.info("Reusing cached topics.")
    if topics is None:
        topics = []

    reporter.update(stage="Done", progress=100, video_status=VideoStatus.DONE)
    return PipelineResult(
        video=video, transcript=transcript, chat_signal=chat_signal,
        topics=topics, warnings=warnings,
    )


REPORT_FILENAME = "report.md"


def run_pipeline(
    url: str,
    *,
    output_dir: str | Path = ".",
    settings: Settings | None = None,
    engine: TranscriptionEngine | None = None,
    llm: SelectionLLM | None = None,
    create_topics: bool = False,
    metadata_fn: Callable = audio_dl.fetch_metadata,
    download_audio_fn: Callable = audio_dl.download_audio,
    download_chat_fn: Callable = chat_downloader.download_chat,
) -> Path:
    """CLI wrapper: FileStore + LogReporter + Markdown report (Phase 0).

    Every artifact is exported to `output_dir`: the JSON caches
    (`<id>.transcript.json`, `<id>.topics.json`) and the Markdown report
    (`report.md`). The intermediate audio goes to a throwaway temp dir and is
    discarded when the run finishes — `data/` is not used by the CLI.

    Topics are only extracted when `create_topics` is True (the `--create-topics`
    CLI flag).
    """
    setup_logging()
    settings = settings or get_settings()
    engine = engine or get_engine(settings)
    llm = llm or get_llm(settings)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="primocut-") as audio_dir:
        result = run_stages(
            url, settings=settings, store=FileStore(settings, base_dir=output_dir),
            reporter=LogReporter(), engine=engine, llm=llm, create_topics=create_topics,
            audio_dir=audio_dir, metadata_fn=metadata_fn,
            download_audio_fn=download_audio_fn, download_chat_fn=download_chat_fn,
        )

    output_path = output_dir / REPORT_FILENAME
    with step(logger, "Markdown report"):
        report = Report(
            video=result.video, transcript_model=result.transcript.model,
            chat_signal=result.chat_signal, topics=result.topics,
            warnings=result.warnings,
        )
        output_path.write_text(render_report(report), encoding="utf-8")

    if result.warnings:
        logger.warning("Report generated with %d degradation warning(s).", len(result.warnings))
    logger.info("Report saved to %s", output_path)
    return output_path
