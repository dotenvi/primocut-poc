"""CRUD tests for own_channels: the user's own channels inside a niche (clip
destinations), owned by exactly one project and scoped so one niche never
touches another's."""

from __future__ import annotations

from fastapi.testclient import TestClient

from primocut.api.app import (
    create_app,
    get_db,
    get_queue_dep,
    get_settings_dep,
    get_youtube_client_dep,
)
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: None
    app.dependency_overrides[get_settings_dep] = lambda: Settings(youtube_api_key="K")
    app.dependency_overrides[get_youtube_client_dep] = lambda: None
    return TestClient(app)


def _project(db, name) -> int:
    with db.session() as s:
        return crepo.create_project(s, name).id


def test_own_channel_crud_and_scoping(tmp_path):
    db = _db(tmp_path)
    a = _project(db, "A")
    b = _project(db, "B")
    c = _client(db)

    r = c.post(f"/api/projects/{a}/own-channels", json={
        "channel_url": "https://youtube.com/@mine", "handle": "@mine",
        "title": "Mine", "role": "main",
    })
    assert r.status_code == 201
    body = r.json()
    own_id = body["id"]
    assert body["role"] == "main" and body["channel_url"] == "https://youtube.com/@mine"

    # Listed under A.
    assert [ch["id"] for ch in c.get(f"/api/projects/{a}/own-channels").json()] == [own_id]

    # Same URL again in A → 409 (unique per project); role is free-form/nullable.
    assert c.post(f"/api/projects/{a}/own-channels",
                  json={"channel_url": "https://youtube.com/@mine"}).status_code == 409

    # Same URL in B is fine (different niche).
    assert c.post(f"/api/projects/{b}/own-channels",
                  json={"channel_url": "https://youtube.com/@mine"}).status_code == 201
    assert c.get(f"/api/projects/{b}/own-channels").json()[0]["role"] is None

    # Deleting A's channel from B is a 404 (scoped), and does not touch A.
    assert c.delete(f"/api/projects/{b}/own-channels/{own_id}").status_code == 404
    assert len(c.get(f"/api/projects/{a}/own-channels").json()) == 1

    # Delete it from its own project.
    assert c.delete(f"/api/projects/{a}/own-channels/{own_id}").status_code == 200
    assert c.get(f"/api/projects/{a}/own-channels").json() == []


def test_own_channel_validation_and_unknown_project(tmp_path):
    db = _db(tmp_path)
    a = _project(db, "A")
    c = _client(db)

    assert c.post(f"/api/projects/{a}/own-channels",
                  json={"channel_url": "   "}).status_code == 400
    assert c.post("/api/projects/999/own-channels",
                  json={"channel_url": "https://youtube.com/@x"}).status_code == 404
    assert c.get("/api/projects/999/own-channels").status_code == 404
