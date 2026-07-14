"""Tests for the Phase 2 API (projects CRUD + channel registration/detach).

Uses TestClient with a temporary DB, a fake queue and a fake YouTube client
injected via dependency_overrides (no network).
"""

from __future__ import annotations

from types import SimpleNamespace

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
from primocut.youtube.client import ChannelInfo, VideoInfo


class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn, args))
        return SimpleNamespace(id="rq1")


class _FakeYouTube:
    def __init__(self, channel_id="UCchan0000000000000000"):
        self._channel_id = channel_id
        self.units_used = 0

    def get_channel(self, **kwargs):
        self.units_used += 1
        return ChannelInfo(
            channel_id=self._channel_id, title="Chan", handle="@chan",
            uploads_playlist_id="UUchan",
        )

    def list_uploads_video_ids(self, *a, **k):
        return []

    def list_videos(self, ids):
        return []


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db, *, queue=None, youtube=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue or _FakeQueue()
    app.dependency_overrides[get_settings_dep] = lambda: Settings(youtube_api_key="K")
    app.dependency_overrides[get_youtube_client_dep] = lambda: youtube or _FakeYouTube()
    return TestClient(app)


# ------------------------- projects CRUD -------------------------


def test_project_crud_flow(tmp_path):
    c = _client(_db(tmp_path))
    r = c.post("/api/projects", json={"name": "Niche", "description": "d"})
    assert r.status_code == 201
    pid = r.json()["id"]

    assert c.post("/api/projects", json={"name": "Niche"}).status_code == 409  # dup

    r = c.get("/api/projects")
    assert r.status_code == 200 and r.json()[0]["channel_count"] == 0

    r = c.patch(f"/api/projects/{pid}", json={"description": "new", "active": False})
    assert r.json()["description"] == "new" and r.json()["active"] is False

    assert c.get(f"/api/projects/{pid}").status_code == 200
    assert c.delete(f"/api/projects/{pid}").status_code == 200
    assert c.get(f"/api/projects/{pid}").status_code == 404


def test_patch_missing_project_404(tmp_path):
    c = _client(_db(tmp_path))
    assert c.patch("/api/projects/999", json={"name": "x"}).status_code == 404


# ------------------------- channel registration -------------------------


def test_add_new_channel_enqueues_backfill(tmp_path):
    db = _db(tmp_path)
    queue = _FakeQueue()
    c = _client(db, queue=queue)
    pid = c.post("/api/projects", json={"name": "P"}).json()["id"]

    r = c.post(f"/api/projects/{pid}/channels", json={"url": "@chan"})
    assert r.status_code == 201
    body = r.json()
    assert body["existing"] is False and body["backfill"] == "queued"
    assert len(queue.calls) == 1  # backfill enqueued

    # Visible via the channels listing with backfill state.
    r = c.get(f"/api/projects/{pid}/channels")
    assert r.status_code == 200
    assert r.json()[0]["channel_id"] == "UCchan0000000000000000"
    assert r.json()[0]["backfill"]["state"] == "queued"


def test_add_existing_channel_dedups_no_enqueue(tmp_path):
    db = _db(tmp_path)
    youtube = _FakeYouTube()
    c = _client(db, youtube=youtube)
    p1 = c.post("/api/projects", json={"name": "P1"}).json()["id"]
    p2 = c.post("/api/projects", json={"name": "P2"}).json()["id"]

    c.post(f"/api/projects/{p1}/channels", json={"url": "@chan"})

    queue2 = _FakeQueue()
    # Re-point the queue dependency for the second call.
    c.app.dependency_overrides[get_queue_dep] = lambda: queue2
    r = c.post(f"/api/projects/{p2}/channels", json={"url": "@chan"})
    assert r.json()["existing"] is True and r.json()["backfill"] == "skipped"
    assert queue2.calls == []
    with db.session() as s:
        assert crepo.count_channel_projects(s, "UCchan0000000000000000") == 2


def test_add_channel_missing_project_404(tmp_path):
    c = _client(_db(tmp_path))
    r = c.post("/api/projects/999/channels", json={"url": "@chan"})
    assert r.status_code == 404


def test_detach_channel_preserves_data(tmp_path):
    db = _db(tmp_path)
    c = _client(db)
    pid = c.post("/api/projects", json={"name": "P"}).json()["id"]
    c.post(f"/api/projects/{pid}/channels", json={"url": "@chan"})
    with db.session() as s:
        crepo.upsert_video(s, video_id="v1", channel_id="UCchan0000000000000000")

    r = c.delete(f"/api/projects/{pid}/channels/UCchan0000000000000000")
    assert r.status_code == 200
    assert r.json()["detached"] is True and r.json()["deactivated"] is True
    with db.session() as s:
        assert crepo.count_channel_videos(s, "UCchan0000000000000000") == 1
        assert crepo.get_channel(s, "UCchan0000000000000000").active is False
