"""Tests for the FastAPI API (TestClient, temporary DB, fake queue, inline worker)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from primocut.api.app import (
    create_app,
    get_db,
    get_queue_dep,
    get_settings_dep,
    get_stopper,
)
from primocut.config import Settings
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import ChatSignal, Peak, Segment, Transcript, Video, VideoStatus
from primocut.worker.jobs import process_video


class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append(args)
        return SimpleNamespace(id="rq1")


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


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db, queue=None) -> TestClient:
    app = create_app()
    data_dir = Path(db.engine.url.database).parent  # same tmp as the database
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue or _FakeQueue()
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)  # no-op without Redis
    app.dependency_overrides[get_settings_dep] = lambda: Settings(data_dir=data_dir)
    return TestClient(app)


def _seed_done(db, tmp_path, vid="v1"):
    with db.session() as s:
        repo.upsert_video(s, Video(id=vid, url=f"https://youtube.com/watch?v={vid}",
                                   title="", channel="", duration=0.0, status=VideoStatus.QUEUED))
    process_video(
        vid, database=db, create_topics=True,
        settings=Settings(data_dir=tmp_path, db_path=tmp_path / "t.db", retry_base_delay=0.0),
        engine=_FakeEngine(), llm=_FakeLLM(), gpu_lock=contextlib.nullcontext(),
        metadata_fn=lambda url: Video(id=vid, url=url, title="T", channel="C", duration=30.0),
        download_audio_fn=lambda url, out_dir, video_id=None: Path("/tmp/fake.wav"),
        download_chat_fn=lambda url: None,
    )


# --------------------------- submit ---------------------------


def test_submit_creates_video_and_enqueues(tmp_path):
    db = _db(tmp_path)
    q = _FakeQueue()
    client = _client(db, q)
    r = client.post("/api/videos", json={"url": "https://www.youtube.com/watch?v=SDUQEa7FAms"})
    assert r.status_code == 201
    assert r.json()["video_id"] == "SDUQEa7FAms"
    assert q.calls == [("SDUQEa7FAms",)]
    with db.session() as s:
        assert repo.get_video(s, "SDUQEa7FAms").status == "queued"


def test_submit_rejects_invalid_url(tmp_path):
    client = _client(_db(tmp_path))
    r = client.post("/api/videos", json={"url": "not a youtube url"})
    assert r.status_code == 400


# --------------------------- list / detail ---------------------------


def test_list_and_detail(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    client = _client(db)
    lst = client.get("/api/videos").json()
    assert any(v["id"] == "v1" and v["status"] == "done" and v["progress"] == 100 for v in lst)
    detail = client.get("/api/videos/v1").json()
    assert detail["title"] == "T"
    assert detail["job"]["status"] == "finished"


def test_detail_404(tmp_path):
    client = _client(_db(tmp_path))
    assert client.get("/api/videos/nope").status_code == 404


# --------------------------- report ---------------------------


def test_report_structure_and_links(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    rep = _client(db).get("/api/videos/v1/report").json()
    assert rep["video"]["title"] == "T"
    assert rep["job"]["status"] == "finished" and rep["job"]["progress"] == 100
    assert rep["topics"][0]["title"] == "A"
    assert rep["topics"][0]["youtube_url"].endswith("&t=0")
    assert "moments" not in rep
    assert len(rep["transcript"]) == 3
    assert rep["transcript"][0]["youtube_url"].startswith("https://youtube.com/watch?v=v1")


def test_report_fire_flag_from_chat_peak(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    with db.session() as s:  # peak at ts=5 falls inside topic A [0,20]
        repo.save_chat_signal(s, ChatSignal(video_id="v1", per_minute=[],
                                            peaks=[Peak(ts=5.0, intensity=50.0, z=3.0)]))
    rep = _client(db).get("/api/videos/v1/report").json()
    assert rep["topics"][0]["fire"] is True


# --------------------------- mark + export ---------------------------


def test_mark_topic_and_export(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    client = _client(db)
    with db.session() as s:
        tid = repo.get_topic_rows(s, "v1")[0].id

    r = client.post(f"/api/videos/v1/topics/{tid}", json={"status": "marked"})
    assert r.status_code == 200

    exp = client.get("/api/videos/v1/export?format=json").json()
    assert exp["cuts"] and exp["cuts"][0]["title"] == "A"
    assert "type" not in exp["cuts"][0]
    assert exp["cuts"][0]["youtube_url"].endswith("&t=0")
    assert "start_hms" in exp["cuts"][0]


def test_mark_invalid_status_and_missing_topic(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    client = _client(db)
    assert client.post("/api/videos/v1/topics/1", json={"status": "xpto"}).status_code == 400
    assert client.post("/api/videos/v1/topics/9999", json={"status": "marked"}).status_code == 404


def test_export_empty_when_nothing_marked(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    exp = _client(db).get("/api/videos/v1/export").json()
    assert exp["cuts"] == []


# --------------------------- reenqueue ---------------------------


def test_reenqueue(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    q = _FakeQueue()
    r = _client(db, q).post("/api/videos/v1/reenqueue")
    assert r.status_code == 202
    assert q.calls == [("v1",)]


# --------------------------- generate topics ---------------------------


def test_generate_topics_endpoint(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)  # done, with topics cached
    q = _FakeQueue()
    r = _client(db, q).post("/api/videos/v1/topics")
    assert r.status_code == 202
    assert q.calls == [("v1",)]  # enqueued a topics run for v1
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "queued"  # reset for processing
        assert repo.get_topic_rows(s, "v1") == []  # cache cleared → will regenerate


def test_generate_topics_404(tmp_path):
    assert _client(_db(tmp_path)).post("/api/videos/nope/topics").status_code == 404


# --------------------------- stop / delete ---------------------------


def test_stop_endpoint(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)
    r = _client(db).post("/api/videos/v1/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "stopped"


def test_stop_404(tmp_path):
    assert _client(_db(tmp_path)).post("/api/videos/nope/stop").status_code == 404


def test_delete_endpoint_purges(tmp_path):
    db = _db(tmp_path)
    _seed_done(db, tmp_path)  # status done
    (tmp_path / "v1.16k.wav").write_bytes(b"x")
    r = _client(db).delete("/api/videos/v1")
    assert r.status_code == 200
    with db.session() as s:
        assert repo.get_video(s, "v1") is None
    assert not (tmp_path / "v1.16k.wav").exists()


def test_delete_refuses_when_active(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, Video(id="v1", url="https://youtube.com/watch?v=v1",
                                   title="", channel="", duration=0.0, status=VideoStatus.QUEUED))
    assert _client(db).delete("/api/videos/v1").status_code == 409
