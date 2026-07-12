"""Orquestração do pipeline, com progresso por etapa.

Fluxo: metadados → áudio → transcrição → chat (opcional) → análise (tópicos +
momentos). `run_stages` é o núcleo reusado por CLI e worker, parametrizado por um
`Store` (cache/persistência) e um `ProgressReporter`. `run_pipeline` é o wrapper
do CLI (Fase 0): usa `FileStore`+`LogReporter` e escreve o relatório Markdown.
Toda a resiliência/degradação (FALLBACKS.md) é preservada.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .analysis.llm import SelectionLLM, get_llm
from .analysis.moments import extract_moments
from .analysis.topics import extract_topics
from .chat import downloader as chat_downloader
from .chat.signals import compute_chat_signal
from .config import Settings, get_settings
from .download import audio as audio_dl
from .logging_conf import get_logger, setup_logging, step
from .models import ChatSignal, CutMoment, Report, Topic, Transcript, Video, VideoStatus
from .progress import LogReporter, ProgressReporter
from .report.markdown import render_report
from .store import FileStore, Store
from .transcription.engine import TranscriptionEngine
from .transcription.faster_whisper_engine import get_engine

logger = get_logger(__name__)


class PipelineError(RuntimeError):
    """Falha em etapa essencial (áudio/transcrição) — aborta com mensagem clara."""


@dataclass
class PipelineResult:
    video: Video
    transcript: Transcript
    chat_signal: ChatSignal | None
    topics: list[Topic]
    moments: list[CutMoment]
    warnings: list[str] = field(default_factory=list)


def run_stages(
    url: str,
    *,
    settings: Settings,
    store: Store,
    reporter: ProgressReporter,
    engine: TranscriptionEngine,
    llm: SelectionLLM,
    metadata_fn: Callable = audio_dl.fetch_metadata,
    download_audio_fn: Callable = audio_dl.download_audio,
    download_chat_fn: Callable = chat_downloader.download_chat,
    gpu_lock=None,
) -> PipelineResult:
    """Núcleo do pipeline: reusa as etapas da Fase 0, com cache via `store`.

    `gpu_lock` (context manager) serializa a transcrição na GPU entre workers.
    CLI/testes passam None → sem lock (no-op).
    """
    warnings: list[str] = []

    # --- Metadados (degradável) ---
    reporter.update(stage="Metadados", progress=5)
    with step(logger, "Metadados"):
        try:
            video = metadata_fn(url)
        except Exception as exc:  # noqa: BLE001 — degrada p/ Video mínimo
            msg = f"Metadados indisponíveis ({type(exc).__name__}: {exc}); usando dados mínimos."
            logger.warning(msg)
            warnings.append(msg)
            video = Video(
                id=audio_dl.extract_video_id(url), url=url,
                title="(desconhecido)", channel="(desconhecido)", duration=0.0,
            )
    store.save_video_meta(video)

    # --- Áudio + Transcrição (essenciais; cache respaldado pelo store) ---
    transcript = store.get_transcript(video.id)
    if transcript is None:
        try:
            reporter.update(stage="Download do áudio", progress=15,
                            video_status=VideoStatus.DOWNLOADING)
            with step(logger, "Download do áudio"):
                audio_path = download_audio_fn(url, settings.data_dir, video_id=video.id)
            reporter.update(stage="Transcrição", progress=30,
                            video_status=VideoStatus.TRANSCRIBING)
            with step(logger, f"Transcrição ({engine.name})"):
                if gpu_lock is not None:
                    logger.info("Aguardando a GPU ficar livre (lock)…")
                with gpu_lock or contextlib.nullcontext():
                    transcript = engine.transcribe(
                        audio_path, video_id=video.id, language=settings.whisper_language
                    )
            store.save_transcript(transcript)
        except Exception as exc:  # essencial → aborta com mensagem clara
            raise PipelineError(str(exc)) from exc
        warnings.extend(getattr(engine, "degradation_warnings", []))
    else:
        logger.info("Reaproveitando transcrição (cache).")

    if video.duration <= 0:  # metadados degradados: preenche pela transcrição
        video.duration = transcript.duration
        store.save_video_meta(video)

    # --- Chat (opcional) ---
    reporter.update(stage="Chat", progress=65)
    chat_signal = store.get_chat_signal(video.id)
    if chat_signal is None:
        with step(logger, "Chat (opcional)"):
            messages = download_chat_fn(url)
            if messages:
                chat_signal = compute_chat_signal(
                    video.id, messages, threshold=settings.chat_zscore_threshold
                )
                store.save_chat_signal(chat_signal)

    # --- Análise (degradável): cache só grava em sucesso completo ---
    reporter.update(stage="Análise: tópicos", progress=70, video_status=VideoStatus.ANALYZING)
    topics = store.get_topics(video.id)
    if topics is None:
        with step(logger, "Análise: tópicos macro"):
            before = len(warnings)
            topics = extract_topics(transcript, llm, settings, warnings=warnings)
            if len(warnings) == before:
                store.save_topics(video.id, topics)
    else:
        logger.info("Reaproveitando tópicos (cache).")

    reporter.update(stage="Análise: momentos", progress=88)
    moments = store.get_moments(video.id)
    if moments is None:
        with step(logger, "Análise: momentos de corte"):
            before = len(warnings)
            moments = extract_moments(transcript, llm, settings, warnings=warnings)
            if len(warnings) == before:
                store.save_moments(video.id, moments)
    else:
        logger.info("Reaproveitando momentos (cache).")

    reporter.update(stage="Concluído", progress=100, video_status=VideoStatus.DONE)
    return PipelineResult(
        video=video, transcript=transcript, chat_signal=chat_signal,
        topics=topics, moments=moments, warnings=warnings,
    )


def run_pipeline(
    url: str,
    *,
    output_path: str | Path = "report.md",
    settings: Settings | None = None,
    engine: TranscriptionEngine | None = None,
    llm: SelectionLLM | None = None,
    metadata_fn: Callable = audio_dl.fetch_metadata,
    download_audio_fn: Callable = audio_dl.download_audio,
    download_chat_fn: Callable = chat_downloader.download_chat,
) -> Path:
    """Wrapper do CLI: FileStore + LogReporter + relatório Markdown (Fase 0)."""
    setup_logging()
    settings = settings or get_settings()
    engine = engine or get_engine(settings)
    llm = llm or get_llm(settings)
    output_path = Path(output_path)

    result = run_stages(
        url, settings=settings, store=FileStore(settings), reporter=LogReporter(),
        engine=engine, llm=llm, metadata_fn=metadata_fn,
        download_audio_fn=download_audio_fn, download_chat_fn=download_chat_fn,
    )

    with step(logger, "Relatório Markdown"):
        report = Report(
            video=result.video, transcript_model=result.transcript.model,
            chat_signal=result.chat_signal, topics=result.topics,
            moments=result.moments, warnings=result.warnings,
        )
        output_path.write_text(render_report(report), encoding="utf-8")

    if result.warnings:
        logger.warning("Relatório gerado com %d aviso(s) de degradação.", len(result.warnings))
    logger.info("Relatório salvo em %s", output_path)
    return output_path
