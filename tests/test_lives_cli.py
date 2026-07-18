"""Tests for the `primocut lives` CLI (submit + list within a project).

`lives add` is exercised at the wiring level (monkeypatching enqueue_video, no
queue/network); `lives list` runs against a temporary DB.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import primocut.db.session as sess
from primocut.cli import app

runner = CliRunner()


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "_db", None)  # reset the get_database singleton
    monkeypatch.setenv("PRIMOCUT_DB_PATH", str(tmp_path / "cli.db"))
    yield
    monkeypatch.setattr(sess, "_db", None)


def test_lives_add_wires_enqueue(monkeypatch):
    calls = {}

    def fake_enqueue(url, *, project_id, **kwargs):
        calls["args"] = (url, project_id)
        return {"video_id": "vid123", "job_id": 1, "rq_job_id": "rq1"}

    monkeypatch.setattr("primocut.worker.queue.enqueue_video", fake_enqueue)
    r = runner.invoke(app, ["lives", "add", "https://youtu.be/vid123", "--project", "7"])
    assert r.exit_code == 0, r.output
    assert calls["args"] == ("https://youtu.be/vid123", 7)
    assert "Submitted" in r.output


def test_lives_add_unknown_project(monkeypatch):
    def fake_enqueue(url, *, project_id, **kwargs):
        raise KeyError(project_id)

    monkeypatch.setattr("primocut.worker.queue.enqueue_video", fake_enqueue)
    r = runner.invoke(app, ["lives", "add", "https://youtu.be/x", "--project", "9"])
    assert r.exit_code == 1
    assert "not found" in r.output.lower()


def test_lives_list_scoped_to_project(temp_db):
    from primocut.db import competitors as crepo
    from primocut.db import get_database
    from primocut.db import repo
    from primocut.models import Video, VideoStatus

    db = get_database()
    with db.session() as s:
        pid = crepo.create_project(s, "Niche").id
        repo.upsert_video(s, Video(id="vid123", url="u", title="My Live", channel="C",
                                   duration=0.0, status=VideoStatus.QUEUED, project_id=pid))

    r = runner.invoke(app, ["lives", "list", "--project", str(pid)])
    assert r.exit_code == 0, r.output
    assert "vid123" in r.output and "My Live" in r.output


def test_lives_list_unknown_project(temp_db):
    r = runner.invoke(app, ["lives", "list", "--project", "999"])
    assert r.exit_code == 1
    assert "not found" in r.output.lower()
