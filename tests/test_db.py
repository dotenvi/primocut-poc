"""Persistence tests (migrations + DAO)."""

from __future__ import annotations

from sqlalchemy import select, text

from primocut.db import repo
from primocut.db.models import Base, SchemaMigration
from primocut.db.session import Database
from primocut.models import (
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


def test_init_creates_schema_and_stamps_versions(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == [1, 2]  # base + drop-moments/english-status migration


def test_migration_upgrades_legacy_db_preserving_topics(tmp_path):
    """A legacy v1 DB (with cut_moments + Portuguese statuses) upgrades cleanly:
    the cut_moments table is dropped, existing topics are preserved and their
    status values are translated to English."""
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    # Build the legacy state by hand: current tables, a leftover cut_moments
    # table, a topic with the old 'marcado' status, and only version 1 stamped.
    Base.metadata.create_all(db.engine)
    with db.session() as s:
        s.add(SchemaMigration(version=1))
        repo.upsert_video(s, _video())
        s.execute(text(
            "CREATE TABLE cut_moments (id INTEGER PRIMARY KEY, video_id TEXT, title TEXT)"
        ))
        s.execute(text("INSERT INTO cut_moments (video_id, title) VALUES ('v1', 'old moment')"))
        s.execute(text(
            "INSERT INTO topics (video_id, ordinal, title, summary, start, \"end\", score, status) "
            "VALUES ('v1', 0, 'kept topic', 's', 0, 5, 0.8, 'marcado')"
        ))

    db.init()  # applies migration 002

    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
        assert versions == [1, 2]
        # cut_moments table is gone
        tables = list(s.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).scalars())
        assert "cut_moments" not in tables
        # topic preserved with translated status
        topics = repo.get_topics(s, "v1")
        assert [t.title for t in topics] == ["kept topic"]
        assert topics[0].status == TopicStatus.MARKED


def test_init_is_idempotent(tmp_path):
    db = _db(tmp_path)
    db.init()
    db.init()  # must not duplicate versions nor fail
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == [1, 2]


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
        repo.set_topic_status(s, tid, "marked")
    with db.session() as s:
        assert repo.get_topics(s, "v1")[0].status == TopicStatus.MARKED


def test_job_lifecycle(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        jid = repo.create_job(s, "v1").id
    with db.session() as s:
        repo.update_job(s, jid, status="started", stage="Transcription", progress=40)
    with db.session() as s:
        j = repo.latest_job(s, "v1")
    assert j.status == "started" and j.progress == 40 and j.stage == "Transcription"


def test_delete_video_cascades(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        repo.upsert_video(s, _video())
        repo.save_transcript(s, Transcript(video_id="v1", language="pt", model="m",
                                           segments=[Segment(index=0, start=0, end=1, text="x")]))
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0, end=1, score=0.5)])
        repo.create_job(s, "v1")
    with db.session() as s:
        assert repo.delete_video(s, "v1") is True
    with db.session() as s:
        assert repo.get_video(s, "v1") is None
        assert repo.get_transcript(s, "v1") is None
        assert repo.get_topics(s, "v1") == []
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
