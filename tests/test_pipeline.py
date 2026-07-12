"""Teste end-to-end do pipeline com todas as dependências pesadas injetadas."""

from __future__ import annotations

from pathlib import Path

import pytest

from primocut.config import Settings
from primocut.models import ChatMessage, Segment, Transcript, Video
from primocut.pipeline import PipelineError, run_pipeline


class _FakeEngine:
    name = "fake-whisper"

    def transcribe(self, audio_path, *, video_id, language="pt"):
        return Transcript(
            video_id=video_id,
            language=language,
            model=self.name,
            segments=[
                Segment(index=0, start=0.0, end=10.0, text="abertura"),
                Segment(index=1, start=10.0, end=20.0, text="assunto principal"),
                Segment(index=2, start=20.0, end=30.0, text="piada boa"),
            ],
        )


class _FakeLLM:
    def select(self, *, system, user, tool, tool_name):
        if tool_name == "record_topics":
            return {"topics": [
                {"title": "Assunto", "summary": "resumo", "start_index": 0, "end_index": 1, "score": 0.7}
            ]}
        return {"moments": [
            {"title": "Piada", "reason": "engraçado", "start_index": 2, "end_index": 2, "score": 0.9}
        ]}


def _video():
    return Video(
        id="abc123",
        url="https://youtube.com/watch?v=abc123",
        title="Live Teste",
        channel="Canal Z",
        duration=30.0,
    )


def test_pipeline_end_to_end_writes_report(tmp_path):
    out = tmp_path / "report.md"
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_path=out,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: [
            ChatMessage(offset_seconds=21.0, text="kkkk", author="x")
            for _ in range(5)
        ],
    )
    assert result == out
    md = out.read_text(encoding="utf-8")
    assert "# Live Teste" in md
    assert "Assunto" in md
    assert "Piada" in md
    assert "watch?v=abc123&t=" in md
    assert "## Atividade do chat" in md


def test_pipeline_without_chat_still_completes(tmp_path):
    out = tmp_path / "report.md"
    run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_path=out,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,  # sem replay
    )
    md = out.read_text(encoding="utf-8")
    assert "Sem replay de chat disponível" in md
    assert "Piada" in md  # análise não depende do chat


class _BoomLLM:
    def select(self, *, system, user, tool, tool_name):
        raise AssertionError("LLM não deveria ser chamado quando há cache")


def test_pipeline_reuses_cached_analysis(tmp_path):
    settings = Settings(anthropic_api_key="x", data_dir=tmp_path)
    common = dict(
        settings=settings,
        engine=_FakeEngine(),
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    # 1ª execução: paga a análise e grava os caches
    run_pipeline("https://youtube.com/watch?v=abc123", output_path=tmp_path / "r1.md",
                 llm=_FakeLLM(), **common)
    assert (tmp_path / "abc123.topics.json").exists()
    assert (tmp_path / "abc123.moments.json").exists()

    # 2ª execução: LLM que explode se chamado — deve usar o cache e concluir
    run_pipeline("https://youtube.com/watch?v=abc123", output_path=tmp_path / "r2.md",
                 llm=_BoomLLM(), **common)
    md = (tmp_path / "r2.md").read_text(encoding="utf-8")
    assert "Assunto" in md and "Piada" in md


def test_pipeline_degrades_when_metadata_fails(tmp_path):
    def boom_meta(url):
        raise RuntimeError("yt-dlp -J falhou")

    out = tmp_path / "r.md"
    run_pipeline(
        "https://youtube.com/watch?v=abc123", output_path=out,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path),
        engine=_FakeEngine(), llm=_FakeLLM(),
        metadata_fn=boom_meta,
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    md = out.read_text(encoding="utf-8")
    assert "⚠️ Execução degradada" in md
    assert "Metadados indisponíveis" in md
    assert "0:30" in md  # duração preenchida a partir da transcrição (30s)


class _PermLLM:
    def select(self, **kwargs):
        raise RuntimeError("crédito acabou")  # permanente


def test_pipeline_degrades_when_analysis_permanently_fails(tmp_path):
    out = tmp_path / "r.md"
    run_pipeline(
        "https://youtube.com/watch?v=abc123", output_path=out,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path, retry_base_delay=0.0),
        engine=_FakeEngine(), llm=_PermLLM(),
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    md = out.read_text(encoding="utf-8")
    assert "⚠️ Execução degradada" in md
    assert "Tópicos: interrompido" in md
    assert not (tmp_path / "abc123.topics.json").exists()  # etapa degradada não cacheia


def test_pipeline_aborts_on_essential_failure(tmp_path):
    def boom_download(url, out_dir, video_id=None):
        raise RuntimeError("não consegui baixar o áudio")

    with pytest.raises(PipelineError):
        run_pipeline(
            "https://youtube.com/watch?v=abc123", output_path=tmp_path / "r.md",
            settings=Settings(anthropic_api_key="x", data_dir=tmp_path),
            engine=_FakeEngine(), llm=_FakeLLM(),
            metadata_fn=lambda url: _video(),
            download_audio_fn=boom_download,
            download_chat_fn=lambda url: None,
        )
