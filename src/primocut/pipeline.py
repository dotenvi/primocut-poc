"""Orquestração do pipeline da Fase 0, com progresso por etapa.

Fluxo: metadados → áudio → transcrição → chat (opcional) → análise (tópicos +
momentos) → relatório Markdown. Todas as dependências pesadas são injetáveis,
para permitir um teste end-to-end sem GPU/rede.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel

from .analysis.llm import SelectionLLM, get_llm
from .analysis.moments import extract_moments
from .analysis.topics import extract_topics
from .chat import downloader as chat_downloader
from .chat.signals import compute_chat_signal
from .config import Settings, get_settings
from .download import audio as audio_dl
from .logging_conf import get_logger, setup_logging, step
from .models import CutMoment, Report, Topic, Transcript, Video, VideoStatus
from .report.markdown import render_report
from .transcription.engine import TranscriptionEngine
from .transcription.faster_whisper_engine import get_engine

logger = get_logger(__name__)

_M = TypeVar("_M", bound=BaseModel)


class PipelineError(RuntimeError):
    """Falha em etapa essencial (áudio/transcrição) — aborta com mensagem clara."""


def _save_cached(path: Path, items: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([i.model_dump() for i in items], ensure_ascii=False), encoding="utf-8"
    )


def _load_cached(path: Path, model: type[_M]) -> list[_M]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [model.model_validate(d) for d in data]


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
    setup_logging()
    settings = settings or get_settings()
    engine = engine or get_engine(settings)
    llm = llm or get_llm(settings)
    output_path = Path(output_path)
    warnings: list[str] = []

    # --- Metadados (degradável) ---
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

    transcript_cache = Path(settings.data_dir) / f"{video.id}.transcript.json"

    if transcript_cache.exists():
        with step(logger, "Transcrição (cache)"):
            transcript = Transcript.model_validate_json(
                transcript_cache.read_text(encoding="utf-8")
            )
            logger.info("Reaproveitando transcrição: %s", transcript_cache)
    else:
        # --- Áudio + Transcrição (essenciais: falha aborta) ---
        try:
            with step(logger, "Download do áudio"):
                audio_path = download_audio_fn(url, settings.data_dir, video_id=video.id)
            with step(logger, f"Transcrição ({engine.name})"):
                video.status = VideoStatus.TRANSCRIBING
                transcript = engine.transcribe(
                    audio_path, video_id=video.id, language=settings.whisper_language
                )
                transcript_cache.parent.mkdir(parents=True, exist_ok=True)
                transcript_cache.write_text(transcript.model_dump_json(), encoding="utf-8")
        except Exception as exc:  # essencial → aborta com mensagem clara
            raise PipelineError(str(exc)) from exc
        warnings.extend(getattr(engine, "degradation_warnings", []))

    # Metadados degradados: preenche a duração a partir da transcrição.
    if video.duration <= 0:
        video.duration = transcript.duration

    with step(logger, "Chat (opcional)"):
        messages = download_chat_fn(url)
        chat_signal = (
            compute_chat_signal(video.id, messages, threshold=settings.chat_zscore_threshold)
            if messages
            else None
        )

    data_dir = Path(settings.data_dir)
    topics_cache = data_dir / f"{video.id}.topics.json"
    moments_cache = data_dir / f"{video.id}.moments.json"

    # --- Análise (degradável): cache só grava em sucesso completo ---
    with step(logger, "Análise: tópicos macro"):
        video.status = VideoStatus.ANALYZING
        if topics_cache.exists():
            topics = _load_cached(topics_cache, Topic)
            logger.info("Reaproveitando tópicos: %s", topics_cache)
        else:
            before = len(warnings)
            topics = extract_topics(transcript, llm, settings, warnings=warnings)
            if len(warnings) == before:  # completo → pode cachear
                _save_cached(topics_cache, topics)

    with step(logger, "Análise: momentos de corte"):
        if moments_cache.exists():
            moments = _load_cached(moments_cache, CutMoment)
            logger.info("Reaproveitando momentos: %s", moments_cache)
        else:
            before = len(warnings)
            moments = extract_moments(transcript, llm, settings, warnings=warnings)
            if len(warnings) == before:
                _save_cached(moments_cache, moments)

    with step(logger, "Relatório Markdown"):
        video.status = VideoStatus.DONE
        report = Report(
            video=video,
            transcript_model=transcript.model,
            chat_signal=chat_signal,
            topics=topics,
            moments=moments,
            warnings=warnings,
        )
        output_path.write_text(render_report(report), encoding="utf-8")

    if warnings:
        logger.warning("Relatório gerado com %d aviso(s) de degradação.", len(warnings))
    logger.info("Relatório salvo em %s", output_path)
    return output_path
