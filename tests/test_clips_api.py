"""API tests for Phase 3 clip rendering: the render trigger (background enqueue,
all vs one), the clip list/status, the download, the explicit delete (row + file),
the clip state folded into the bench, and the leakage guard."""

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
from primocut.db import clips as clips_repo
from primocut.db import competitors as crepo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus


class _RecordingQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return SimpleNamespace(id="rq1")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db, queue=None):
    app = create_app()
    data_dir = Path(db.engine.url.database).parent
    clips_dir = data_dir / "clips"
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue or _RecordingQueue()
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)
    app.dependency_overrides[get_settings_dep] = lambda: Settings(
        data_dir=data_dir, clips_dir=clips_dir)
    return TestClient(app), clips_dir


def _seed(db, video_id="vid00000001", duration=600.0, n_topics=2, project="N"):
    with db.session() as s:
        pid = crepo.create_project(s, project).id
        repo.upsert_video(s, Video(
            id=video_id, url=f"https://y/{video_id}", title="Live", channel="C",
            duration=duration, status=VideoStatus.DONE, project_id=pid,
        ))
        repo.save_topics(s, video_id, [
            Topic(title=f"T{i}", summary="", start=float(i * 100 + 100),
                  end=float(i * 100 + 160), score=0.5)
            for i in range(n_topics)
        ])
        tids = [t.id for t in repo.get_topic_rows(s, video_id)]
    return pid, video_id, tids


def _base(pid, vid):
    return f"/api/projects/{pid}/lives/{vid}"


def _add_items(c, b, cid, tids):
    for t in tids:
        c.post(f"{b}/cuts/{cid}/items", json={"topic_id": t})


def _seed_done_clip(db, clips_dir, vid, cid, item_id, position=0, slug="t0"):
    filename = f"{position + 1:03d}-{slug}.mp4"
    rel = f"{vid}/{cid}/{filename}"
    path = Path(clips_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"clip-bytes")
    with db.session() as s:
        clip = clips_repo.reset_to_queued(
            s, cut_item_id=item_id, video_id=vid, cut_id=cid, position=position,
            filename=filename, rel_path=rel, phash="h", padding_s=15.0,
            profile="nvenc", start_s=100.0, end_s=160.0,
        )
        clips_repo.set_state(s, clip.id, "done", encoder_used="nvenc", size_bytes=10)
        clip_id = clip.id
    return clip_id, path


def test_render_enqueues_background_job_for_all_items(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=2)
    q = _RecordingQueue()
    c, _ = _client(db, queue=q)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    _add_items(c, b, cid, tids)

    r = c.post(f"{b}/cuts/{cid}/render", json={})
    assert r.status_code == 202
    assert r.json()["queued"] == 2
    # Exactly one background job (render_clips) enqueued.
    assert len(q.calls) == 1
    fn, args, _ = q.calls[0]
    assert fn.__name__ == "render_clips"
    assert args[0] == vid and args[1] == cid


def test_render_single_item(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=2)
    q = _RecordingQueue()
    c, _ = _client(db, queue=q)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    _add_items(c, b, cid, tids)
    item_id = c.get(f"{b}/cuts/{cid}").json()["items"][0]["id"]

    r = c.post(f"{b}/cuts/{cid}/render", json={"item_id": item_id})
    assert r.json()["queued"] == 1
    _, args, _ = q.calls[0]
    assert args[2] == [item_id]  # only the one item


def test_list_download_and_delete_clip(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=1)
    c, clips_dir = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    _add_items(c, b, cid, tids)
    item_id = c.get(f"{b}/cuts/{cid}").json()["items"][0]["id"]
    clip_id, path = _seed_done_clip(db, clips_dir, vid, cid, item_id)

    # List shows the clip with a download URL.
    clips = c.get(f"{b}/cuts/{cid}/clips").json()
    assert len(clips) == 1 and clips[0]["state"] == "done"
    assert clips[0]["download_url"].endswith(f"/clips/{clip_id}/download")

    # Download returns the file bytes.
    d = c.get(f"{b}/cuts/{cid}/clips/{clip_id}/download")
    assert d.status_code == 200 and d.content == b"clip-bytes"

    # Delete removes both the row and the file.
    assert c.delete(f"{b}/cuts/{cid}/clips/{clip_id}").status_code == 204
    assert not path.exists()
    assert c.get(f"{b}/cuts/{cid}/clips").json() == []


def test_bench_folds_clip_state_into_items(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=1)
    c, clips_dir = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    _add_items(c, b, cid, tids)
    item_id = c.get(f"{b}/cuts/{cid}").json()["items"][0]["id"]

    # No clip yet → item.clip is null.
    assert c.get(f"{b}/cuts/{cid}").json()["items"][0]["clip"] is None

    _seed_done_clip(db, clips_dir, vid, cid, item_id)
    it = c.get(f"{b}/cuts/{cid}").json()["items"][0]
    assert it["clip"]["state"] == "done"
    assert it["clip"]["download_url"] is not None


def test_removing_item_deletes_its_clip_file(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=1)
    c, clips_dir = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    _add_items(c, b, cid, tids)
    item_id = c.get(f"{b}/cuts/{cid}").json()["items"][0]["id"]
    _, path = _seed_done_clip(db, clips_dir, vid, cid, item_id)
    assert path.exists()

    # Removing the item (an explicit action) takes its clip file with it.
    c.delete(f"{b}/cuts/{cid}/items/{item_id}")
    assert not path.exists()
    with db.session() as s:
        assert clips_repo.clip_for_item(s, item_id) is None  # row cascaded


def test_render_routes_respect_the_leakage_guard(tmp_path):
    db = _db(tmp_path)
    pid_a, vid_a, _ = _seed(db, video_id="vidaaaa00001", project="A")
    with db.session() as s:
        pid_b = crepo.create_project(s, "B").id
    c, _ = _client(db)
    other = f"/api/projects/{pid_b}/lives/{vid_a}"

    assert c.post(f"{other}/cuts/1/render", json={}).status_code == 404
    assert c.get(f"{other}/cuts/1/clips").status_code == 404
    assert c.get(f"{other}/cuts/1/clips/1/download").status_code == 404
    assert c.delete(f"{other}/cuts/1/clips/1").status_code == 404
