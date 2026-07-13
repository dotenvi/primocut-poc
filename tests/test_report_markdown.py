"""Tests for the Markdown report rendering."""

from __future__ import annotations

from primocut.models import (
    ChatSignal,
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
            title="Test Podcast",
            channel="Channel X",
            duration=230.0,
        ),
        transcript_model="faster-whisper:large-v3-turbo",
        chat_signal=chat,
        topics=[
            Topic(title="About Python", summary="they talk about python", start=100.0, end=130.0, score=0.8),
            Topic(title="Coffee", summary="types of coffee", start=200.0, end=230.0, score=0.4),
        ],
    )


def test_report_has_metadata_and_sections():
    md = render_report(_report())
    assert "# Test Podcast" in md
    assert "Channel X" in md
    assert "faster-whisper:large-v3-turbo" in md
    assert "## Chat activity" in md
    assert "## Macro topics (2)" in md


def test_report_has_clickable_timestamp_links():
    md = render_report(_report())
    # link in the format required by the spec: ...&t=SECONDS
    assert "https://youtube.com/watch?v=abc123&t=100" in md
    assert "https://youtube.com/watch?v=abc123&t=200" in md


def test_topic_crossing_peak_gets_fire_marker():
    md = render_report(_report())
    lines = md.splitlines()
    # topic 100–130 covers the peak at 120 -> 🔥; topic 200–230 does not
    python_line = next(l for l in lines if "About Python" in l)
    cafe_line = next(l for l in lines if "Coffee" in l)
    assert "🔥" in python_line
    assert "🔥" not in cafe_line


def test_chat_section_has_sparkline_and_table():
    md = render_report(_report())
    assert "Message density per minute:" in md
    assert "| Minute | Msgs | Laughs | Emotes |" in md
    assert "z=3.2" in md


def test_report_without_chat_shows_notice():
    md = render_report(_report(with_chat=False))
    assert "No chat replay available" in md
    assert "Message density per minute:" not in md


def test_report_renders_degradation_warnings():
    r = _report()
    r.warnings = ["Topics: stopped at window 2/3 — out of credit"]
    md = render_report(r)
    assert "## ⚠️ Degraded run" in md
    assert "window 2/3" in md


def test_report_without_warnings_omits_section():
    md = render_report(_report())
    assert "Degraded run" not in md
