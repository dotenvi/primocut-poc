"""API tests for Phase 3 cut export: the background enqueue, the export state
folded into the bench, the zip download, and the leakage guard."""

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
from primocut.db import exports as exports_repo
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
    settings = Settings(data_dir=data_dir, clips_dir=data_dir / "clips",
                        exports_dir=data_dir / "exports")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue or _RecordingQueue()
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    return TestClient(app), settings


def _seed(db, video_id="vid00000001", n_topics=2, project="N"):
    with db.session() as s:
        pid = crepo.create_project(s, project).id
        repo.upsert_video(s, Video(
            id=video_id, url=f"https://y/{video_id}", title="Live", channel="C",
            duration=600.0, status=VideoStatus.DONE, project_id=pid,
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


def _cut_with_items(c, b, tids):
    cid = c.get(f"{b}/cuts").json()[0]["id"]
    for t in tids:
        c.post(f"{b}/cuts/{cid}/items", json={"topic_id": t})
    return cid


def test_export_enqueues_background_job(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=2)
    q = _RecordingQueue()
    c, _ = _client(db, queue=q)
    b = _base(pid, vid)
    cid = _cut_with_items(c, b, tids)

    r = c.post(f"{b}/cuts/{cid}/export", json={"render_missing": True, "upscale_1440p": True})
    assert r.status_code == 202
    assert r.json()["queued"] == 2
    assert len(q.calls) == 1
    fn, args, kwargs = q.calls[0]
    assert fn.__name__ == "export_cut"
    assert args[0] == vid and args[1] == cid
    assert kwargs["render_missing"] is True and kwargs["upscale_1440p"] is True

    # The export row is queued right away (UI shows a live state); it lands in bench.
    bench = c.get(f"{b}/cuts/{cid}").json()
    assert bench["export"]["state"] == "queued"


def test_export_download_when_ready(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=1)
    c, settings = _client(db)
    b = _base(pid, vid)
    cid = _cut_with_items(c, b, tids)

    # Simulate a finished export: a done row + a real zip on disk.
    zip_rel = f"{vid}/{cid}/cut-1-20260720.zip"
    zip_path = settings.exports_dir / zip_rel
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(b"PK\x03\x04zip")
    with db.session() as s:
        exports_repo.reset_to_queued(s, video_id=vid, cut_id=cid, item_count=1,
                                     skip_master=False, upscaled=False)
        exports_repo.set_state(s, cid, "done", package_rel_path=f"{vid}/{cid}/cut-1-20260720",
                               zip_rel_path=zip_rel)

    # The bench exposes a download URL once ready.
    exp = c.get(f"{b}/cuts/{cid}").json()["export"]
    assert exp["state"] == "done" and exp["download_url"]

    r = c.get(exp["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content == b"PK\x03\x04zip"


def test_export_download_404_before_ready(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=1)
    c, _ = _client(db)
    b = _base(pid, vid)
    cid = _cut_with_items(c, b, tids)
    # No export yet.
    assert c.get(f"{b}/cuts/{cid}/export/download").status_code == 404
    # Status endpoint reports "none".
    assert c.get(f"{b}/cuts/{cid}/export").json()["state"] == "none"


def test_export_leakage_guard(tmp_path):
    db = _db(tmp_path)
    pid_a, vid_a, tids_a = _seed(db, video_id="vidaaaaaaaa", project="A")
    pid_b, _, _ = _seed(db, video_id="vidbbbbbbbb", project="B")
    c, _ = _client(db)
    b = _base(pid_a, vid_a)
    cid = _cut_with_items(c, b, tids_a)
    # The same cut, addressed through the WRONG project, is a 404.
    wrong = f"/api/projects/{pid_b}/lives/{vid_a}"
    assert c.post(f"{wrong}/cuts/{cid}/export", json={}).status_code == 404
    assert c.get(f"{wrong}/cuts/{cid}/export").status_code == 404
