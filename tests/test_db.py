"""Testes da persistência (migrations + DAO)."""

from __future__ import annotations

from sqlalchemy import select

from primocut.db import repo
from primocut.db.models import SchemaMigration
from primocut.db.session import Database
from primocut.models import (
    CutMoment,
    Segment,
    Topic,
    TopicStatus,
    Transcript,
    Video,
    VideoStatus,
)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _video(vid: str = "v1") -> Video:
    return Video(id=vid, url="http://x", title="T", channel="C", duration=10.0)


def test_init_creates_schema_and_stamps_base_version(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == [1]


def test_init_is_idempotent(tmp_path):
    db = _db(tmp_path)
    db.init()
    db.init()  # não pode duplicar versão nem falhar
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == [1]


def test_video_crud_and_status(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
    with db.session() as s:
        assert repo.get_video(s, "v1").title == "T"
        repo.set_video_status(s, "v1", VideoStatus.DONE)
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "done"
        assert repo.video_to_model(repo.get_video(s, "v1")).status == VideoStatus.DONE


def test_transcript_roundtrip(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_transcript(s, Transcript(
            video_id="v1", language="pt", model="m",
            segments=[Segment(index=0, start=0.0, end=5.0, text="oi")],
        ))
    with db.session() as s:
        t = repo.get_transcript(s, "v1")
    assert t is not None and t.segments[0].text == "oi"


def test_topics_save_and_mark(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0.0, end=5.0, score=0.8)])
    with db.session() as s:
        tid = repo.get_topic_rows(s, "v1")[0].id
        repo.set_topic_status(s, tid, "marcado")
    with db.session() as s:
        assert repo.get_topics(s, "v1")[0].status == TopicStatus.MARKED


def test_moments_save_and_mark(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_moments(s, "v1", [CutMoment(title="P", reason="r", start=1.0, end=2.0, score=0.9)])
    with db.session() as s:
        mid = repo.get_moment_rows(s, "v1")[0].id
        repo.set_moment_status(s, mid, "descartado")
    with db.session() as s:
        assert repo.get_moments(s, "v1")[0].status == TopicStatus.DISCARDED


def test_job_lifecycle(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        jid = repo.create_job(s, "v1").id
    with db.session() as s:
        repo.update_job(s, jid, status="started", stage="Transcrição", progress=40)
    with db.session() as s:
        j = repo.latest_job(s, "v1")
    assert j.status == "started" and j.progress == 40 and j.stage == "Transcrição"


def test_delete_video_cascades(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_transcript(s, Transcript(video_id="v1", language="pt", model="m",
                                           segments=[Segment(index=0, start=0, end=1, text="x")]))
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0, end=1, score=0.5)])
        repo.save_moments(s, "v1", [CutMoment(title="P", reason="r", start=0, end=1, score=0.5)])
        repo.create_job(s, "v1")
    with db.session() as s:
        assert repo.delete_video(s, "v1") is True
    with db.session() as s:
        assert repo.get_video(s, "v1") is None
        assert repo.get_transcript(s, "v1") is None
        assert repo.get_topics(s, "v1") == []
        assert repo.get_moments(s, "v1") == []
        assert repo.latest_job(s, "v1") is None
    with db.session() as s:
        assert repo.delete_video(s, "inexistente") is False


def test_save_topics_replaces_previous(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0, end=5, score=0.5)])
    with db.session() as s:
        repo.save_topics(s, "v1", [Topic(title="B", summary="s", start=0, end=5, score=0.5)])
    with db.session() as s:
        titles = [t.title for t in repo.get_topics(s, "v1")]
    assert titles == ["B"]
