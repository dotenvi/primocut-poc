"""Leakage tests: a live is contained in exactly one project and never appears
in another project's queries (1 live -> 1 project, not M:N)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from primocut.api.app import (
    create_app,
    get_db,
    get_queue_dep,
    get_settings_dep,
    get_stopper,
)
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database

_URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
_URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


class _FakeQueue:
    def enqueue(self, fn, *args, **kwargs):
        return SimpleNamespace(id="rq1")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db) -> TestClient:
    app = create_app()
    data_dir = Path(db.engine.url.database).parent
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: _FakeQueue()
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)
    app.dependency_overrides[get_settings_dep] = lambda: Settings(data_dir=data_dir)
    return TestClient(app)


def _two_projects(db) -> tuple[int, int]:
    with db.session() as s:
        return crepo.create_project(s, "A").id, crepo.create_project(s, "B").id


def test_live_never_leaks_across_projects(tmp_path):
    db = _db(tmp_path)
    a, b = _two_projects(db)
    c = _client(db)

    live_a = c.post(f"/api/projects/{a}/lives", json={"url": _URL_A}).json()["video_id"]
    c.post(f"/api/projects/{b}/lives", json={"url": _URL_B})

    # A sees only its own; B does not see A's.
    assert [v["id"] for v in c.get(f"/api/projects/{a}/lives").json()] == [live_a]
    assert live_a not in [v["id"] for v in c.get(f"/api/projects/{b}/lives").json()]

    # Every /lives/{lid} route on the wrong project is a 404, not a cross-read.
    assert c.get(f"/api/projects/{b}/lives/{live_a}").status_code == 404
    assert c.get(f"/api/projects/{b}/lives/{live_a}/report").status_code == 404
    assert c.delete(f"/api/projects/{b}/lives/{live_a}").status_code == 404
    assert c.post(f"/api/projects/{b}/lives/{live_a}/reenqueue").status_code == 404
    assert c.post(f"/api/projects/{b}/lives/{live_a}/topics").status_code == 404
    assert c.post(f"/api/projects/{b}/lives/{live_a}/stop").status_code == 404

    # The right project still reaches it.
    assert c.get(f"/api/projects/{a}/lives/{live_a}").status_code == 200


def test_live_belongs_to_exactly_one_project(tmp_path):
    """The live carries a single scalar project_id (containment), not an M:N set."""
    db = _db(tmp_path)
    a, b = _two_projects(db)
    live_a = _client(db).post(f"/api/projects/{a}/lives", json={"url": _URL_A}).json()["video_id"]

    from primocut.db import repo

    with db.session() as s:
        assert repo.get_video(s, live_a).project_id == a
        assert repo.get_project_live(s, b, live_a) is None
