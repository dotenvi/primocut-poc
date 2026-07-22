"""Persistence tests (migrations + DAO)."""

from __future__ import annotations

from sqlalchemy import select, text

from primocut.db import competitors as crepo
from primocut.db import repo
from primocut.db.models import Base, CompetitorVideoRow, SchemaMigration
from primocut.db.session import Database
from primocut.models import (
    Segment,
    Topic,
    Transcript,
    Video,
    VideoStatus,
)

_LEGACY_VERSIONS = [1, 2, 3, 4, 5, 6]
_ALL_VERSIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _video(vid: str = "v1", project_id: int = 1) -> Video:
    return Video(id=vid, url="http://x", title="T", channel="C", duration=10.0,
                 project_id=project_id)


def _seed_project(s, name: str = "P") -> int:
    """A live needs a niche to live in (project_id is NOT NULL)."""
    return crepo.create_project(s, name).id


def _legacy_db(tmp_path):
    """A pre-007 DB: `videos` has no `project_id` column, stamped up to v6.

    The legacy `videos` table is created by hand first (no project_id, no FK), so
    the subsequent create_all() leaves it untouched (it only makes missing
    tables) — that reproduces an old database that predates the column."""
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    with db.session() as s:
        s.execute(text(
            "CREATE TABLE videos (id VARCHAR PRIMARY KEY, url VARCHAR, title VARCHAR, "
            "channel VARCHAR, duration FLOAT, status VARCHAR, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
    Base.metadata.create_all(db.engine)
    with db.session() as s:
        for v in _LEGACY_VERSIONS:
            s.add(SchemaMigration(version=v))
    return db


def _insert_legacy_live(s, vid: str = "v1") -> None:
    s.execute(text(
        "INSERT INTO videos (id, url, title, channel, duration, status, "
        "created_at, updated_at) VALUES "
        f"('{vid}', 'u', '', '', 0, 'queued', "
        "'2020-01-01 00:00:00.000000', '2020-01-01 00:00:00.000000')"
    ))


# --------------------------- schema / migrations ---------------------------


def test_init_creates_schema_and_stamps_versions(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == _ALL_VERSIONS


def test_migration_upgrades_legacy_db_preserving_topics(tmp_path):
    """A legacy v1 DB (with cut_moments + a topics.status column) upgrades
    cleanly: the cut_moments table is dropped, the topics.status column is
    dropped, and existing topics are otherwise preserved."""
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(db.engine)
    with db.session() as s:
        s.add(SchemaMigration(version=1))
        _seed_project(s)
        repo.upsert_video(s, _video())
        s.execute(text("ALTER TABLE topics ADD COLUMN status TEXT DEFAULT 'new'"))
        s.execute(text(
            "CREATE TABLE cut_moments (id INTEGER PRIMARY KEY, video_id TEXT, title TEXT)"
        ))
        s.execute(text("INSERT INTO cut_moments (video_id, title) VALUES ('v1', 'old moment')"))
        s.execute(text(
            "INSERT INTO topics (video_id, ordinal, title, summary, start, \"end\", score, status) "
            "VALUES ('v1', 0, 'kept topic', 's', 0, 5, 0.8, 'marcado')"
        ))

    db.init()  # applies migrations 002..007

    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
        assert versions == _ALL_VERSIONS
        tables = list(s.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).scalars())
        assert "cut_moments" not in tables
        cols = [r[1] for r in s.execute(text("PRAGMA table_info(topics)")).all()]
        assert "status" not in cols
        assert [t.title for t in repo.get_topics(s, "v1")] == ["kept topic"]


def test_009_backfills_empty_competitor_thumbnail_url(tmp_path):
    """A pre-009 DB with competitor videos missing their thumbnail url gets the
    canonical hqdefault url derived from the video id; a real stored url is left
    untouched."""
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(db.engine)
    with db.session() as s:
        for v in [1, 2, 3, 4, 5, 6, 7, 8]:
            s.add(SchemaMigration(version=v))
        crepo.upsert_channel(s, channel_id="c1")
        crepo.upsert_video(s, video_id="vEmpty", channel_id="c1")  # thumbnail_url ""
        crepo.upsert_video(
            s, video_id="vReal", channel_id="c1",
            thumbnail_url="https://i.ytimg.com/vi/vReal/maxresdefault.jpg",
        )

    db.init()  # applies migration 009 + backfill

    with db.session() as s:
        assert list(s.execute(select(SchemaMigration.version)).scalars()) == _ALL_VERSIONS
        rows = {
            r.video_id: r.thumbnail_url
            for r in s.execute(select(CompetitorVideoRow)).scalars()
        }
        assert rows["vEmpty"] == "https://i.ytimg.com/vi/vEmpty/hqdefault.jpg"
        assert rows["vReal"] == "https://i.ytimg.com/vi/vReal/maxresdefault.jpg"


def test_init_is_idempotent(tmp_path):
    db = _db(tmp_path)
    db.init()
    db.init()  # must not duplicate versions nor fail
    with db.session() as s:
        versions = list(s.execute(select(SchemaMigration.version)).scalars())
    assert versions == _ALL_VERSIONS


def test_007_empty_base_creates_no_default_project(tmp_path):
    """On a fresh/empty base the backfill is a no-op: no orphan lives means no
    'Default' project is ever invented."""
    db = _db(tmp_path)
    with db.session() as s:
        assert crepo.list_projects(s) == []
        assert list(s.execute(select(SchemaMigration.version)).scalars()) == _ALL_VERSIONS


def test_007_backfills_orphan_lives_to_oldest_project(tmp_path):
    db = _legacy_db(tmp_path)
    with db.session() as s:
        oldest = _seed_project(s, "old")   # id 1, created first
        _seed_project(s, "new")            # id 2
        _insert_legacy_live(s, "v1")
        _insert_legacy_live(s, "v2")

    db.init()  # migration 007 + backfill

    with db.session() as s:
        assert list(s.execute(select(SchemaMigration.version)).scalars()) == _ALL_VERSIONS
        assert repo.get_video(s, "v1").project_id == oldest
        assert repo.get_video(s, "v2").project_id == oldest
        # no NULL project_id survives the backfill
        orphans = s.execute(text("SELECT COUNT(*) FROM videos WHERE project_id IS NULL")).scalar()
        assert orphans == 0


def test_007_creates_default_when_no_project_exists(tmp_path):
    db = _legacy_db(tmp_path)
    with db.session() as s:
        _insert_legacy_live(s, "v1")

    db.init()

    with db.session() as s:
        projects = crepo.list_projects(s)
        assert [p.name for p in projects] == ["Default"]
        assert repo.get_video(s, "v1").project_id == projects[0].id


# --------------------------- DAO ---------------------------


def test_video_crud_and_status(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
    with db.session() as s:
        assert repo.get_video(s, "v1").title == "T"
        assert repo.get_video(s, "v1").project_id == 1
        repo.set_video_status(s, "v1", VideoStatus.DONE)
    with db.session() as s:
        assert repo.get_video(s, "v1").status == "done"
        assert repo.video_to_model(repo.get_video(s, "v1")).status == VideoStatus.DONE


def test_list_project_lives_is_scoped(tmp_path):
    """A live of one project never shows up in another project's list."""
    db = _db(tmp_path)
    with db.session() as s:
        a = _seed_project(s, "A")
        b = _seed_project(s, "B")
        repo.upsert_video(s, _video("va", project_id=a))
        repo.upsert_video(s, _video("vb", project_id=b))
    with db.session() as s:
        assert [v.id for v in repo.list_project_lives(s, a)] == ["va"]
        assert [v.id for v in repo.list_project_lives(s, b)] == ["vb"]
        assert repo.get_project_live(s, a, "vb") is None      # leakage guard
        assert repo.get_project_live(s, a, "va").id == "va"


def test_transcript_roundtrip(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
        repo.save_transcript(s, Transcript(
            video_id="v1", language="pt", model="m",
            segments=[Segment(index=0, start=0.0, end=5.0, text="oi")],
        ))
    with db.session() as s:
        t = repo.get_transcript(s, "v1")
    assert t is not None and t.segments[0].text == "oi"


def test_topics_save_and_get(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0.0, end=5.0, score=0.8)])
    with db.session() as s:
        topics = repo.get_topics(s, "v1")
        assert [(t.title, t.score) for t in topics] == [("A", 0.8)]


def test_job_lifecycle(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
        jid = repo.create_job(s, "v1").id
    with db.session() as s:
        repo.update_job(s, jid, status="started", stage="Transcription", progress=40)
    with db.session() as s:
        j = repo.latest_job(s, "v1")
    assert j.status == "started" and j.progress == 40 and j.stage == "Transcription"


def test_append_stage_records_transitions_and_dedups(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
        jid = repo.create_job(s, "v1").id
    with db.session() as s:
        repo.append_stage(s, jid, "Metadata")
        repo.append_stage(s, jid, "Metadata")  # consecutive duplicate is ignored
        repo.append_stage(s, jid, "Transcription")
        repo.append_stage(s, jid, "__end__")
    with db.session() as s:
        hist = repo.latest_job(s, "v1").stage_history
    assert [h["stage"] for h in hist] == ["Metadata", "Transcription", "__end__"]
    assert all("at" in h for h in hist)  # every transition is timestamped


def test_delete_video_cascades(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
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


def test_deleting_project_cascades_its_lives(tmp_path):
    """Containment: a project owns its lives, so deleting it removes them."""
    db = _db(tmp_path)
    with db.session() as s:
        pid = _seed_project(s)
        repo.upsert_video(s, _video("v1", project_id=pid))
    with db.session() as s:
        crepo.delete_project(s, pid)
    with db.session() as s:
        assert repo.get_video(s, "v1") is None


def test_save_topics_replaces_previous(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _seed_project(s)
        repo.upsert_video(s, _video())
        repo.save_topics(s, "v1", [Topic(title="A", summary="s", start=0, end=5, score=0.5)])
    with db.session() as s:
        repo.save_topics(s, "v1", [Topic(title="B", summary="s", start=0, end=5, score=0.5)])
    with db.session() as s:
        titles = [t.title for t in repo.get_topics(s, "v1")]
    assert titles == ["B"]
