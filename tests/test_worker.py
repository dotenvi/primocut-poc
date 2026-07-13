"""Tests for the RQ worker (inline execution, no real Redis)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from primocut.config import Settings
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Segment, Transcript, Video, VideoStatus
from primocut.pipeline import PipelineError
from primocut.worker.jobs import process_video
from primocut.worker.queue import (
    VideoBusyError,
    delete_video,
    enqueue_video,
    stop_video,
)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


class _FakeEngine:
    name = "fake"
    degradation_warnings: list[str] = []

    def transcribe(self, audio_path, *, video_id, language="pt"):
        return Transcript(
            video_id=video_id, language=language, model=self.name,
            segments=[Segment(index=i, start=i * 10.0, end=(i + 1) * 10.0, text=f"s{i}")
                      for i in range(3)],
        )


class _FakeLLM:
    def select(self, *, system, user, tool, tool_name):
        return {"topics": [{"title": "A", "summary": "s", "start_index": 0, "end_index": 1, "score": 0.7}]}


def _seed_video(db, vid="v1"):
    with db.session() as s:
        repo.upsert_video(s, Video(
            id=vid, url=f"https://youtube.com/watch?v={vid}", title="", channel="",
            duration=0.0, status=VideoStatus.QUEUED,
        ))


def _settings(tmp_path, **kw):
    return Settings(data_dir=tmp_path, db_path=tmp_path / "t.db", retry_base_delay=0.0, **kw)


def _common(tmp_path):
    return dict(
        settings=_settings(tmp_path),
        engine=_FakeEngine(),
        llm=_FakeLLM(),
        create_topics=True,
        gpu_lock=contextlib.nullcontext(),  # no Redis in tests
        metadata_fn=lambda url: Video(id="v1", url=url, title="T", channel="C", duration=30.0),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )


def test_process_video_success_transitions(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)
    res = process_video("v1", database=db, **_common(tmp_path))
    assert res["topics"] == 1
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "done"
        job = repo.latest_job(s, "v1")
        assert job.status == "finished" and job.progress == 100
        assert repo.get_transcript(s, "v1") is not None
        assert [t.title for t in repo.get_topics(s, "v1")] == ["A"]


def test_process_video_audio_uses_tempdir_not_data_dir(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)
    seen: dict[str, Path] = {}
    common = _common(tmp_path)

    def capture(url, out_dir, video_id=None):
        seen["audio_dir"] = Path(out_dir)
        return Path("/tmp/fake.wav")

    common["download_audio_fn"] = capture
    process_video("v1", database=db, **common)

    assert seen["audio_dir"] != tmp_path  # data_dir is not used for audio
    assert not seen["audio_dir"].exists()  # temp dir cleaned up when the job ends


def test_process_video_skips_topics_by_default(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)

    class _BoomLLM:
        def select(self, **k):
            raise AssertionError("the LLM must not be called without create_topics")

    common = _common(tmp_path)
    del common["create_topics"]  # default → no topics
    common["llm"] = _BoomLLM()
    res = process_video("v1", database=db, **common)
    assert res["topics"] == 0
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "done"
        assert repo.get_topic_rows(s, "v1") == []  # nothing generated


def test_generate_topics_enqueues_with_flag(tmp_path):
    from types import SimpleNamespace

    from primocut.worker.queue import generate_topics

    db = _db(tmp_path)
    _seed_video(db)

    class _FakeQueue:
        def __init__(self):
            self.calls = []

        def enqueue(self, fn, *args, **kwargs):
            self.calls.append((args, kwargs))
            return SimpleNamespace(id="rq1")

    fq = _FakeQueue()
    res = generate_topics("v1", settings=_settings(tmp_path), database=db, queue=fq)
    assert res["video_id"] == "v1"
    args, kwargs = fq.calls[0]
    assert args == ("v1",)
    assert kwargs["create_topics"] is True
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "queued"


def test_generate_topics_missing_raises(tmp_path):
    from primocut.worker.queue import generate_topics

    db = _db(tmp_path)
    with pytest.raises(KeyError):
        generate_topics("nope", settings=_settings(tmp_path), database=db, queue=object())


def test_gpu_lock_acquired_once_during_transcription(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)

    class _FakeLock:
        def __init__(self):
            self.entered = 0
            self.exited = 0

        def __enter__(self):
            self.entered += 1
            return self

        def __exit__(self, *a):
            self.exited += 1
            return False

    lock = _FakeLock()
    common = _common(tmp_path)
    common["gpu_lock"] = lock
    process_video("v1", database=db, **common)
    assert lock.entered == 1 and lock.exited == 1  # acquired/released only during transcription


def test_reenqueue_is_idempotent_and_respects_cache(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)
    process_video("v1", database=db, **_common(tmp_path))  # 1st: persists everything

    # 2nd run: engine/LLM/download that blow up if called → cache must skip everything
    class _BoomEngine:
        name = "boom"
        def transcribe(self, *a, **k):
            raise AssertionError("should not re-transcribe")

    class _BoomLLM:
        def select(self, **k):
            raise AssertionError("should not re-call the LLM")

    def _boom_dl(url, out_dir, video_id=None):
        raise AssertionError("should not re-download audio")

    process_video(
        "v1", database=db, settings=_settings(tmp_path),
        engine=_BoomEngine(), llm=_BoomLLM(),
        metadata_fn=lambda url: Video(id="v1", url=url, title="T", channel="C", duration=30.0),
        download_audio_fn=_boom_dl, download_chat_fn=lambda url: None,
    )
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "done"  # finished without reprocessing


def test_process_video_essential_failure_sets_error(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)

    def _boom_dl(url, out_dir, video_id=None):
        raise RuntimeError("no audio")

    common = _common(tmp_path)
    common["download_audio_fn"] = _boom_dl
    with pytest.raises(PipelineError):
        process_video("v1", database=db, **common)
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "error"
        assert repo.latest_job(s, "v1").status == "failed"


def test_degradation_is_persisted_in_job_warnings(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)

    class _PermLLM:
        def select(self, **k):
            raise RuntimeError("out of credit")  # permanent

    common = _common(tmp_path)
    common["llm"] = _PermLLM()
    process_video("v1", database=db, **common)
    with db.session() as s:
        job = repo.latest_job(s, "v1")
        assert job.status == "finished"          # degraded, did not abort
        assert job.warnings                       # warnings persisted
        assert repo.get_video(s, "v1").status == "done"


_NOOP_STOP = lambda *a, **k: None  # noqa: E731


def test_stop_video_marks_stopped(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)
    res = stop_video("v1", settings=_settings(tmp_path), database=db, stopper=_NOOP_STOP)
    assert res["status"] == "stopped"
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "stopped"


def test_stop_video_missing_raises(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(KeyError):
        stop_video("nope", settings=_settings(tmp_path), database=db, stopper=_NOOP_STOP)


def test_delete_video_purges_db(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)
    with db.session() as s:
        repo.save_transcript(s, Transcript(video_id="v1", language="pt", model="m", segments=[]))
        repo.set_video_status(s, "v1", VideoStatus.DONE)

    res = delete_video("v1", settings=_settings(tmp_path), database=db, stopper=_NOOP_STOP)
    assert res == {"video_id": "v1"}  # no files to purge (audio was in a temp dir)
    with db.session() as s:
        assert repo.get_video(s, "v1") is None
        assert repo.get_transcript(s, "v1") is None


def test_delete_video_refuses_when_active(tmp_path):
    db = _db(tmp_path)
    _seed_video(db)  # status queued (ativo)
    with pytest.raises(VideoBusyError):
        delete_video("v1", settings=_settings(tmp_path), database=db, stopper=_NOOP_STOP)


def test_enqueue_creates_rows_and_enqueues(tmp_path):
    db = _db(tmp_path)

    class _FakeQueue:
        def __init__(self):
            self.calls = []

        def enqueue(self, fn, *args, **kwargs):
            self.calls.append((fn, args))
            return SimpleNamespace(id="rq1")

    fq = _FakeQueue()
    res = enqueue_video(
        "https://www.youtube.com/watch?v=SDUQEa7FAms",
        settings=_settings(tmp_path), database=db, queue=fq,
    )
    assert res["video_id"] == "SDUQEa7FAms"
    assert fq.calls and fq.calls[0][1] == ("SDUQEa7FAms",)
    with db.session() as s:
        assert repo.get_video(s, "SDUQEa7FAms").status == "queued"
        assert repo.latest_job(s, "SDUQEa7FAms").rq_job_id == "rq1"
