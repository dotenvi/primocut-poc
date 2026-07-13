"""End-to-end pipeline test with all the heavy dependencies injected."""

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
                Segment(index=0, start=0.0, end=10.0, text="opening"),
                Segment(index=1, start=10.0, end=20.0, text="main subject"),
                Segment(index=2, start=20.0, end=30.0, text="good joke"),
            ],
        )


class _FakeLLM:
    def select(self, *, system, user, tool, tool_name):
        return {"topics": [
            {"title": "Subject", "summary": "summary", "start_index": 0, "end_index": 1, "score": 0.7}
        ]}


def _video():
    return Video(
        id="abc123",
        url="https://youtube.com/watch?v=abc123",
        title="Test Live",
        channel="Channel Z",
        duration=30.0,
    )


def test_pipeline_end_to_end_writes_report(tmp_path):
    out_dir = tmp_path / "out"
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: [
            ChatMessage(offset_seconds=21.0, text="kkkk", author="x")
            for _ in range(5)
        ],
    )
    assert result == out_dir / "report.md"
    md = result.read_text(encoding="utf-8")
    assert "# Test Live" in md
    assert "Subject" in md
    assert "watch?v=abc123&t=" in md
    assert "## Chat activity" in md


def test_pipeline_writes_artifacts_to_output_dir_not_data_dir(tmp_path):
    """The CLI path exports every artifact to `output_dir`; `data_dir` is unused
    and the audio lands in a throwaway temp dir (neither output nor data)."""
    out_dir = tmp_path / "out"
    data_dir = tmp_path / "data"
    seen: dict[str, Path] = {}

    def capture_download(url, out_dir, video_id=None):
        seen["audio_dir"] = Path(out_dir)
        return Path("/tmp/fake.wav")

    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=data_dir),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=capture_download,
        download_chat_fn=lambda url: None,
    )
    # Final artifacts live in the output dir.
    assert result == out_dir / "report.md"
    assert (out_dir / "report.md").exists()
    assert (out_dir / "abc123.transcript.json").exists()
    assert (out_dir / "abc123.topics.json").exists()
    # Audio went to a temp dir — not the output dir, not data_dir.
    assert seen["audio_dir"] != out_dir
    assert seen["audio_dir"] != data_dir
    # data_dir is never used by the CLI path.
    assert not data_dir.exists() or not any(data_dir.iterdir())


def test_pipeline_defaults_output_to_current_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    assert result == Path("report.md")
    assert (tmp_path / "report.md").exists()


def test_pipeline_without_chat_still_completes(tmp_path):
    out_dir = tmp_path / "out"
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,  # no replay
    )
    md = result.read_text(encoding="utf-8")
    assert "No chat replay available" in md
    assert "Subject" in md  # analysis does not depend on chat


def test_pipeline_skips_topics_by_default(tmp_path):
    out_dir = tmp_path / "out"
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123",
        output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(),
        llm=_BoomLLM(),  # must NOT be called without --create-topics
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    md = result.read_text(encoding="utf-8")
    assert "## Macro topics (0)" in md
    assert "_No topic identified._" in md
    assert not (out_dir / "abc123.topics.json").exists()


class _BoomLLM:
    def select(self, *, system, user, tool, tool_name):
        raise AssertionError("the LLM should not be called when there is a cache")


def test_pipeline_reuses_cached_analysis(tmp_path):
    out_dir = tmp_path / "out"
    common = dict(
        output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(),
        create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    # 1st run: pays for the analysis and writes the caches
    run_pipeline("https://youtube.com/watch?v=abc123", llm=_FakeLLM(), **common)
    assert (out_dir / "abc123.topics.json").exists()

    # 2nd run: an LLM that blows up if called — must use the cache and finish
    result = run_pipeline("https://youtube.com/watch?v=abc123", llm=_BoomLLM(), **common)
    md = result.read_text(encoding="utf-8")
    assert "Subject" in md


def test_pipeline_degrades_when_metadata_fails(tmp_path):
    def boom_meta(url):
        raise RuntimeError("yt-dlp -J failed")

    result = run_pipeline(
        "https://youtube.com/watch?v=abc123", output_dir=tmp_path / "out",
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
        engine=_FakeEngine(), llm=_FakeLLM(), create_topics=True,
        metadata_fn=boom_meta,
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    md = result.read_text(encoding="utf-8")
    assert "⚠️ Degraded run" in md
    assert "Metadata unavailable" in md
    assert "0:30" in md  # duration filled in from the transcript (30s)


class _PermLLM:
    def select(self, **kwargs):
        raise RuntimeError("out of credit")  # permanent


def test_pipeline_degrades_when_analysis_permanently_fails(tmp_path):
    out_dir = tmp_path / "out"
    result = run_pipeline(
        "https://youtube.com/watch?v=abc123", output_dir=out_dir,
        settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data", retry_base_delay=0.0),
        engine=_FakeEngine(), llm=_PermLLM(), create_topics=True,
        metadata_fn=lambda url: _video(),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )
    md = result.read_text(encoding="utf-8")
    assert "⚠️ Degraded run" in md
    assert "Topics: stopped" in md
    assert not (out_dir / "abc123.topics.json").exists()  # a degraded stage is not cached


def test_pipeline_aborts_on_essential_failure(tmp_path):
    def boom_download(url, out_dir, video_id=None):
        raise RuntimeError("could not download the audio")

    with pytest.raises(PipelineError):
        run_pipeline(
            "https://youtube.com/watch?v=abc123", output_dir=tmp_path / "out",
            settings=Settings(anthropic_api_key="x", data_dir=tmp_path / "data"),
            engine=_FakeEngine(), llm=_FakeLLM(),
            metadata_fn=lambda url: _video(),
            download_audio_fn=boom_download,
            download_chat_fn=lambda url: None,
        )
