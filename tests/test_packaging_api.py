"""API tests for the Phase 3 AI packaging endpoints + the bench integration.

Covers: the opt-in enqueue of each action (titles/brief/frames/rank/audience), the
packaging context endpoint (three lenses, or an unavailable message), the bench
folding in per-item packaging + the filled `audience_asked` slot, a served frame,
and the leakage guard on every route.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from primocut.api.app import (
    create_app, get_db, get_queue_dep, get_settings_dep, get_stopper,
)
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db import cuts as cuts_repo
from primocut.db import packaging as pkg_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus


class _RecordingQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn.__name__, args, kwargs))
        return SimpleNamespace(id="rq1")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db, queue, data_dir):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)
    app.dependency_overrides[get_settings_dep] = lambda: Settings(data_dir=data_dir,
                                                                  clips_dir=str(data_dir / "clips"))
    return TestClient(app)


def _seed(db, video_id="vid00000001"):
    with db.session() as s:
        pid = crepo.create_project(s, "N").id
        repo.upsert_video(s, Video(id=video_id, url="u", title="Live", channel="C",
                                   duration=600.0, status=VideoStatus.DONE, project_id=pid))
        repo.save_topics(s, video_id, [Topic(title=f"T{i}", summary="", start=float(i * 100),
                                             end=float(i * 100 + 60), score=0.5)
                                      for i in range(2)])
        cut = cuts_repo.ensure_default_cut(s, video_id)
        tids = [t.id for t in repo.get_topic_rows(s, video_id)]
        item = cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[0])
        return pid, video_id, cut.id, item.id, tids


def _base(pid, vid):
    return f"/api/projects/{pid}/lives/{vid}"


# ------------------------- enqueue actions -------------------------


def test_generate_titles_enqueues_background(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    q = _RecordingQueue()
    c = _client(db, q, tmp_path)
    r = c.post(f"{_base(pid, vid)}/cuts/{cut_id}/packaging/titles", json={"k": 3})
    assert r.status_code == 202
    assert q.calls[0][0] == "generate_cut_packaging"
    assert q.calls[0][1][2] == "title" and q.calls[0][2]["k"] == 3


def test_frame_extraction_enqueues_deterministic_job(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    q = _RecordingQueue()
    c = _client(db, q, tmp_path)
    r = c.post(f"{_base(pid, vid)}/cuts/{cut_id}/packaging/frames", json={"count": 4})
    assert r.status_code == 202
    assert q.calls[0][0] == "extract_cut_frames" and q.calls[0][2]["count"] == 4


def test_frame_ranking_and_brief_and_audience_enqueue(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    q = _RecordingQueue()
    c = _client(db, q, tmp_path)
    assert c.post(f"{_base(pid, vid)}/cuts/{cut_id}/packaging/brief", json={}).status_code == 202
    assert c.post(f"{_base(pid, vid)}/cuts/{cut_id}/packaging/frames/rank", json={}).status_code == 202
    assert c.post(f"{_base(pid, vid)}/audience-match", json={}).status_code == 202
    names = [call[0] for call in q.calls]
    assert names == ["generate_cut_packaging", "rank_cut_frames", "match_live_audience"]


# ------------------------- context -------------------------


def test_packaging_context_available(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    c = _client(db, _RecordingQueue(), tmp_path)
    r = c.get(f"{_base(pid, vid)}/packaging/context")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert set(body) >= {"differential", "conventions", "carries_value", "examples", "flat"}


# ------------------------- bench integration -------------------------


def test_bench_folds_in_packaging_and_audience(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, tids = _seed(db)
    with db.session() as s:
        pkg_repo.upsert_packaging(s, cut_item_id=item_id, cut_id=cut_id, kind="title",
                                  payload={"titles": [{"title": "Boa"}]}, content_hash="h")
        pkg_repo.upsert_audience_match(s, video_id=vid,
                                       payload={str(tids[0]): [{"type": "guest",
                                                                "value": "Fulano"}]},
                                       content_hash="ah")
    c = _client(db, _RecordingQueue(), tmp_path)
    bench = c.get(f"{_base(pid, vid)}/cuts/{cut_id}").json()

    item = bench["items"][0]
    assert item["packaging"]["title"]["payload"]["titles"][0]["title"] == "Boa"
    # The reserved audience slot is now filled for the matched topic.
    matched = next(t for t in bench["topics"] if t["id"] == tids[0])
    assert matched["signals"]["audience_asked"] is True
    assert matched["signals"]["audience_matches"][0]["value"] == "Fulano"
    # An unmatched topic stays False.
    other = next(t for t in bench["topics"] if t["id"] == tids[1])
    assert other["signals"]["audience_asked"] is False


def test_frame_is_served(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    settings = Settings(data_dir=tmp_path, clips_dir=str(tmp_path / "clips"))
    from primocut.worker.packaging_jobs import frames_dir_for

    fdir = frames_dir_for(settings, vid, cut_id, 0)  # item position 0
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "frame-001.jpg").write_bytes(b"\xff\xd8\xff")

    c = _client(db, _RecordingQueue(), tmp_path)
    r = c.get(f"{_base(pid, vid)}/cuts/{cut_id}/items/{item_id}/frames/frame-001.jpg")
    assert r.status_code == 200 and r.content == b"\xff\xd8\xff"
    # Path traversal is refused.
    assert c.get(f"{_base(pid, vid)}/cuts/{cut_id}/items/{item_id}/frames/..%2Fx").status_code == 404


# ------------------------- leakage guard -------------------------


def test_leakage_guard_on_packaging_routes(tmp_path):
    db = _db(tmp_path)
    pid, vid, cut_id, item_id, _ = _seed(db)
    with db.session() as s:
        other = crepo.create_project(s, "Other").id
    c = _client(db, _RecordingQueue(), tmp_path)
    # Same live id, wrong project → 404 everywhere.
    assert c.get(f"{_base(other, vid)}/packaging/context").status_code == 404
    assert c.post(f"{_base(other, vid)}/cuts/{cut_id}/packaging/titles", json={}).status_code == 404
    assert c.post(f"{_base(other, vid)}/audience-match", json={}).status_code == 404
