"""Tests for the Phase 2 CLI (projects + competitors sub-apps).

Projects commands run against a temporary DB (via PRIMOCUT_DB_PATH + a reset of
the get_database singleton). The competitors commands are exercised at the
wiring level, monkeypatching the orchestration functions (no network/queue).
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


def test_projects_add_list_remove(temp_db):
    r = runner.invoke(app, ["projects", "add", "MyNiche", "-d", "desc"])
    assert r.exit_code == 0, r.output
    assert "Project created" in r.output

    r = runner.invoke(app, ["projects", "list"])
    assert r.exit_code == 0
    assert "MyNiche" in r.output

    # Duplicate name fails.
    r = runner.invoke(app, ["projects", "add", "MyNiche"])
    assert r.exit_code == 1

    r = runner.invoke(app, ["projects", "remove", "1"])
    assert r.exit_code == 0
    assert "deleted" in r.output.lower()

    r = runner.invoke(app, ["projects", "remove", "1"])
    assert r.exit_code == 1  # already gone


def test_competitors_add_wires_register(monkeypatch):
    calls = {}

    def fake_register(project_id, url, **kwargs):
        calls["args"] = (project_id, url)
        return {
            "channel_id": "UCabc",
            "title": "Chan",
            "existing": False,
            "backfill": "queued",
            "rq_job_id": "rq1",
        }

    monkeypatch.setattr("primocut.worker.channels.register_channel", fake_register)
    r = runner.invoke(app, ["competitors", "add", "@chan", "--project", "7"])
    assert r.exit_code == 0, r.output
    assert calls["args"] == (7, "@chan")
    assert "Registered" in r.output


def test_competitors_detach_wires_detach(monkeypatch):
    def fake_detach(project_id, channel_id, **kwargs):
        assert (project_id, channel_id) == (7, "UCabc")
        return {"channel_id": channel_id, "detached": True, "deactivated": True}

    monkeypatch.setattr("primocut.worker.channels.detach_channel", fake_detach)
    r = runner.invoke(app, ["competitors", "detach", "UCabc", "--project", "7"])
    assert r.exit_code == 0, r.output
    assert "Detached" in r.output
    assert "deactivated" in r.output.lower()


def test_competitors_snapshot_wires_cycle(temp_db, monkeypatch):
    calls = {}

    def fake_cycle(scope, *, channel_id=None, **kwargs):
        calls["scope"] = scope
        calls["channel_id"] = channel_id
        return {"scope": scope, "channels": 2, "videos": 5,
                "packaging_changes": 1, "quota_units": 9}

    monkeypatch.setattr("primocut.worker.snapshot_jobs.snapshot_cycle", fake_cycle)
    r = runner.invoke(app, ["competitors", "snapshot", "--scope", "recent", "-c", "UCabc"])
    assert r.exit_code == 0, r.output
    assert calls == {"scope": "recent", "channel_id": "UCabc"}
    assert "Snapshot (recent) done" in r.output
    assert "9 quota" in r.output  # rich may wrap "quota unit(s)" across lines


def test_competitors_snapshot_rejects_bad_scope(temp_db):
    r = runner.invoke(app, ["competitors", "snapshot", "--scope", "weekly"])
    assert r.exit_code == 2
