"""Tests for the audio cleanup (retention)."""

from __future__ import annotations

import os
import time

from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Transcript, Segment, Video, VideoStatus
from primocut.worker.cleanup import cleanup_audio


def _touch(path, age_days: float):
    path.write_bytes(b"fake audio")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_deletes_old_audio_keeps_recent(tmp_path):
    old = tmp_path / "abc.16k.wav"
    old_raw = tmp_path / "abc.wav"
    recent = tmp_path / "xyz.16k.wav"
    _touch(old, age_days=10)
    _touch(old_raw, age_days=10)
    _touch(recent, age_days=1)

    deleted = cleanup_audio(tmp_path, retention_days=7)

    assert set(deleted) == {old, old_raw}
    assert not old.exists() and not old_raw.exists()
    assert recent.exists()  # recent audio preserved


def test_never_touches_transcript_or_json(tmp_path):
    # permanent artifacts: cache JSON and the database
    (tmp_path / "abc.transcript.json").write_text("{}")
    (tmp_path / "abc.topics.json").write_text("[]")
    audio = tmp_path / "abc.16k.wav"
    _touch(audio, age_days=30)

    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    with db.session() as s:
        repo.upsert_video(s, Video(id="abc", url="u", title="T", channel="C",
                                   duration=1.0, status=VideoStatus.DONE))
        repo.save_transcript(s, Transcript(video_id="abc", language="pt", model="m",
                                           segments=[Segment(index=0, start=0, end=1, text="oi")]))

    cleanup_audio(tmp_path, retention_days=7)

    assert not audio.exists()  # old audio removed
    assert (tmp_path / "abc.transcript.json").exists()  # JSON stays
    assert (tmp_path / "abc.topics.json").exists()
    with db.session() as s:  # transcript in the database intact
        assert repo.get_transcript(s, "abc") is not None


def test_missing_dir_is_safe(tmp_path):
    assert cleanup_audio(tmp_path / "does-not-exist", retention_days=7) == []


def test_now_override_controls_cutoff(tmp_path):
    a = tmp_path / "a.wav"
    a.write_bytes(b"x")
    os.utime(a, (1000.0, 1000.0))  # very old mtime (epoch 1000)
    # with now just after, retention 0 days → deletes
    assert cleanup_audio(tmp_path, retention_days=0, now=2000.0) == [a]
