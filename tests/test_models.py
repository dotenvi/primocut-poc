"""Testes dos modelos pydantic e helpers de timestamp."""

from __future__ import annotations

import pytest

from primocut.models import (
    CutMoment,
    Report,
    Segment,
    Topic,
    TopicStatus,
    Transcript,
    Video,
    VideoStatus,
    youtube_url,
)


def _transcript() -> Transcript:
    return Transcript(
        video_id="abc123",
        language="pt",
        model="large-v3-turbo",
        segments=[
            Segment(index=0, start=0.0, end=5.0, text="olá pessoal"),
            Segment(index=1, start=5.0, end=12.0, text="hoje vamos falar de python"),
            Segment(index=2, start=12.0, end=20.5, text="e também de café"),
        ],
    )


def test_youtube_url_uses_integer_seconds():
    assert youtube_url("abc123", 90.7) == "https://youtube.com/watch?v=abc123&t=90"
    assert youtube_url("abc123", 0.0) == "https://youtube.com/watch?v=abc123&t=0"


def test_transcript_duration_is_last_end():
    assert _transcript().duration == 20.5


def test_resolve_span_maps_indices_to_seconds():
    t = _transcript()
    start, end = t.resolve_span(1, 2)
    assert start == 5.0
    assert end == 20.5


def test_resolve_span_clamps_out_of_range():
    t = _transcript()
    start, end = t.resolve_span(-5, 999)
    assert start == 0.0
    assert end == 20.5


def test_resolve_span_swaps_when_reversed():
    t = _transcript()
    start, end = t.resolve_span(2, 0)
    assert start == 0.0
    assert end == 20.5


def test_indexed_text_includes_index_and_timestamp():
    line = _transcript().indexed_text().splitlines()[1]
    assert line.startswith("[1]")
    assert "python" in line


def test_video_status_enum():
    v = Video(id="abc123", url="http://x", title="T", channel="C", duration=100.0)
    assert v.status == VideoStatus.QUEUED
    assert VideoStatus.DONE.value == "done"


def test_topic_defaults_to_new_status():
    t = Topic(title="Python", summary="fala de python", start=5.0, end=12.0, score=0.8)
    assert t.status == TopicStatus.NEW


def test_report_youtube_url_uses_video_id():
    t = _transcript()
    report = Report(
        video=Video(id="abc123", url="http://x", title="T", channel="C", duration=20.5),
        transcript_model="large-v3-turbo",
        chat_signal=None,
        topics=[Topic(title="Python", summary="s", start=5.0, end=12.0, score=0.8)],
        moments=[CutMoment(title="corte", reason="engraçado", start=12.0, end=20.5, score=0.9)],
    )
    assert report.youtube_url(5.0) == "https://youtube.com/watch?v=abc123&t=5"
    assert report.chat_signal is None


def test_score_must_be_between_0_and_1():
    with pytest.raises(ValueError):
        Topic(title="x", summary="s", start=0, end=1, score=1.5)
