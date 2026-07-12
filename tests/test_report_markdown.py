"""Testes da renderização do relatório Markdown."""

from __future__ import annotations

from primocut.models import (
    ChatSignal,
    CutMoment,
    MinuteBucket,
    Peak,
    Report,
    Topic,
    Video,
)
from primocut.report.markdown import render_report


def _report(with_chat: bool = True) -> Report:
    chat = None
    if with_chat:
        chat = ChatSignal(
            video_id="abc123",
            per_minute=[
                MinuteBucket(minute=0, msgs=2, laughs=0, emotes=0),
                MinuteBucket(minute=1, msgs=3, laughs=1, emotes=0),
                MinuteBucket(minute=2, msgs=50, laughs=10, emotes=5),
            ],
            peaks=[Peak(ts=120.0, intensity=50.0, z=3.2)],
        )
    return Report(
        video=Video(
            id="abc123",
            url="https://youtube.com/watch?v=abc123",
            title="Podcast Teste",
            channel="Canal X",
            duration=230.0,
        ),
        transcript_model="faster-whisper:large-v3-turbo",
        chat_signal=chat,
        topics=[
            Topic(title="Sobre Python", summary="falam de python", start=100.0, end=130.0, score=0.8),
            Topic(title="Café", summary="tipos de café", start=200.0, end=230.0, score=0.4),
        ],
        moments=[
            CutMoment(title="Piada", reason="engraçado", start=118.0, end=125.0, score=0.95),
        ],
    )


def test_report_has_metadata_and_sections():
    md = render_report(_report())
    assert "# Podcast Teste" in md
    assert "Canal X" in md
    assert "faster-whisper:large-v3-turbo" in md
    assert "## Atividade do chat" in md
    assert "## Tópicos macro (2)" in md
    assert "## Momentos candidatos a corte (1)" in md


def test_report_has_clickable_timestamp_links():
    md = render_report(_report())
    # link no formato exigido pela spec: ...&t=SEGUNDOS
    assert "https://youtube.com/watch?v=abc123&t=100" in md
    assert "https://youtube.com/watch?v=abc123&t=200" in md
    assert "https://youtube.com/watch?v=abc123&t=118" in md  # momento


def test_topic_and_moment_crossing_peak_get_fire_marker():
    md = render_report(_report())
    lines = md.splitlines()
    # tópico 100–130 cobre o pico em 120 -> 🔥; tópico 200–230 não
    python_line = next(l for l in lines if "Sobre Python" in l)
    cafe_line = next(l for l in lines if "Café" in l)
    piada_line = next(l for l in lines if "Piada" in l)
    assert "🔥" in python_line
    assert "🔥" not in cafe_line
    assert "🔥" in piada_line


def test_chat_section_has_sparkline_and_table():
    md = render_report(_report())
    assert "Densidade de mensagens/minuto:" in md
    assert "| Minuto | Msgs | Risadas | Emotes |" in md
    assert "z=3.2" in md


def test_report_without_chat_shows_notice():
    md = render_report(_report(with_chat=False))
    assert "Sem replay de chat disponível" in md
    assert "Densidade de mensagens/minuto:" not in md


def test_report_renders_degradation_warnings():
    r = _report()
    r.warnings = ["Tópicos: interrompido na janela 2/3 — crédito acabou"]
    md = render_report(r)
    assert "## ⚠️ Execução degradada" in md
    assert "janela 2/3" in md


def test_report_without_warnings_omits_section():
    md = render_report(_report())
    assert "Execução degradada" not in md
