"""API tests for the Phase 3 cut room: cuts/items/reorder/triage endpoints, the
one-shot bench payload (no N+1), the leakage guard, and the guarantee that the
decision bench dispatches NO rendering job."""

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
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus


class _RecordingQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append(fn)
        return SimpleNamespace(id="rq1")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _client(db, queue=None) -> TestClient:
    app = create_app()
    data_dir = Path(db.engine.url.database).parent
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue or _RecordingQueue()
    app.dependency_overrides[get_stopper] = lambda: (lambda *a, **k: None)
    app.dependency_overrides[get_settings_dep] = lambda: Settings(data_dir=data_dir)
    return TestClient(app)


def _seed(db, video_id="vid00000001", duration=600.0, n_topics=3, project="N"):
    with db.session() as s:
        pid = crepo.create_project(s, project).id
        repo.upsert_video(s, Video(
            id=video_id, url=f"https://y/{video_id}", title="Live", channel="C",
            duration=duration, status=VideoStatus.DONE, project_id=pid,
        ))
        repo.save_topics(s, video_id, [
            Topic(title=f"T{i}", summary="", start=float(i * 100),
                  end=float(i * 100 + 60), score=0.5)
            for i in range(n_topics)
        ])
        tids = [t.id for t in repo.get_topic_rows(s, video_id)]
    return pid, video_id, tids


def _base(pid, vid):
    return f"/api/projects/{pid}/lives/{vid}"


def test_cuts_crud_and_bench_flow(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db, n_topics=3)
    c = _client(db)
    b = _base(pid, vid)

    # GET /cuts creates the default Cut 1 lazily.
    cuts = c.get(f"{b}/cuts").json()
    assert len(cuts) == 1 and cuts[0]["name"] == "Cut 1"
    cid = cuts[0]["id"]

    # Bench: every topic present with its derived state (open by default).
    bench = c.get(f"{b}/cuts/{cid}").json()
    assert {t["state"] for t in bench["topics"]} == {"open"}
    assert bench["total_duration_s"] == 0

    # Add two topic items -> both read `in`, composition reflects them.
    c.post(f"{b}/cuts/{cid}/items", json={"topic_id": tids[0]})
    bench = c.post(f"{b}/cuts/{cid}/items", json={"topic_id": tids[1]}).json()
    assert [it["topic_id"] for it in bench["items"]] == [tids[0], tids[1]]
    state = {t["id"]: t["state"] for t in bench["topics"]}
    assert state[tids[0]] == "in" and state[tids[1]] == "in" and state[tids[2]] == "open"
    assert bench["total_duration_s"] == 120

    # Reorder transactionally via the ordered id list.
    item_ids = [it["id"] for it in bench["items"]]
    bench = c.patch(f"{b}/cuts/{cid}/items", json={"item_ids": list(reversed(item_ids))}).json()
    assert [it["id"] for it in bench["items"]] == list(reversed(item_ids))

    # Remove an item -> the topic falls back to open.
    bench = c.delete(f"{b}/cuts/{cid}/items/{item_ids[0]}").json()
    assert {t["id"]: t["state"] for t in bench["topics"]}[tids[0]] == "open"


def test_manual_range_item_and_validation(tmp_path):
    db = _db(tmp_path)
    pid, vid, _ = _seed(db, duration=600.0)
    c = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]

    bench = c.post(f"{b}/cuts/{cid}/items", json={"start_s": 10, "end_s": 40}).json()
    assert bench["items"][0]["topic_id"] is None
    assert bench["items"][0]["title"] == "Manual range"

    # end <= start and beyond the live duration are 400s.
    assert c.post(f"{b}/cuts/{cid}/items", json={"start_s": 40, "end_s": 10}).status_code == 400
    assert c.post(f"{b}/cuts/{cid}/items", json={"start_s": 500, "end_s": 700}).status_code == 400


def test_triage_endpoint_cycle_and_rejections(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db)
    c = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]

    def state_of(tid):
        bench = c.get(f"{b}/cuts/{cid}").json()
        return {t["id"]: t["state"] for t in bench["topics"]}[tid]

    assert c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "maybe"}).status_code == 200
    assert state_of(tids[0]) == "maybe"
    c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "discarded"})
    assert state_of(tids[0]) == "discarded"
    c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "open"})
    assert state_of(tids[0]) == "open"

    # `in` may never be set through triage; an unknown state is a 400 too.
    assert c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "in"}).status_code == 400
    assert c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "x"}).status_code == 400


def test_in_wins_over_triage_in_derived_state(tmp_path):
    """A topic both `in` the cut and carrying a `maybe` triage reads `in` (item
    precedence) — and removing the item falls back to the persisted `maybe`."""
    db = _db(tmp_path)
    pid, vid, tids = _seed(db)
    c = _client(db)
    b = _base(pid, vid)
    cid = c.get(f"{b}/cuts").json()[0]["id"]

    c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "maybe"})
    c.post(f"{b}/cuts/{cid}/items", json={"topic_id": tids[0]})
    bench = c.get(f"{b}/cuts/{cid}").json()
    assert {t["id"]: t["state"] for t in bench["topics"]}[tids[0]] == "in"

    item_id = bench["items"][0]["id"]
    bench = c.delete(f"{b}/cuts/{cid}/items/{item_id}").json()
    assert {t["id"]: t["state"] for t in bench["topics"]}[tids[0]] == "maybe"


def test_two_cuts_share_triage_but_not_membership(tmp_path):
    db = _db(tmp_path)
    pid, vid, tids = _seed(db)
    c = _client(db)
    b = _base(pid, vid)
    cut_a = c.get(f"{b}/cuts").json()[0]["id"]
    cut_b = c.post(f"{b}/cuts", json={"name": "Cut 2"}).json()["id"]

    c.patch(f"{b}/topics/{tids[0]}/triage", json={"state": "maybe"})
    c.post(f"{b}/cuts/{cut_a}/items", json={"topic_id": tids[0]})

    in_a = {t["id"]: t["state"] for t in c.get(f"{b}/cuts/{cut_a}").json()["topics"]}
    in_b = {t["id"]: t["state"] for t in c.get(f"{b}/cuts/{cut_b}").json()["topics"]}
    assert in_a[tids[0]] == "in"
    assert in_b[tids[0]] == "maybe"


def test_bench_has_no_n_plus_one(tmp_path):
    """The bench payload is assembled with a bounded number of queries: a live with
    many more topics must not cost more SQL than a small one (same cut/item shape)."""
    db = _db(tmp_path)
    small = _seed(db, video_id="vidsmall0001", n_topics=2, project="S")
    big = _seed(db, video_id="vidbig000001", n_topics=40, project="B")
    c = _client(db)

    counter = {"n": 0}
    from sqlalchemy import event

    @event.listens_for(db.engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    def bench_query_count(pid, vid):
        c.get(f"/api/projects/{pid}/lives/{vid}/cuts")  # ensure default cut (excluded)
        counter["n"] = 0
        c.get(f"/api/projects/{pid}/lives/{vid}/cuts/"
              f"{c.get(f'/api/projects/{pid}/lives/{vid}/cuts').json()[0]['id']}")
        return counter["n"]

    # The count must not grow with the number of topics.
    assert bench_query_count(*small[:2]) == bench_query_count(*big[:2])


def test_no_render_job_dispatched(tmp_path):
    """The whole decision-bench flow must enqueue nothing (rendering is a later
    step): the recording queue stays empty."""
    db = _db(tmp_path)
    pid, vid, tids = _seed(db)
    q = _RecordingQueue()
    c = _client(db, queue=q)
    b = _base(pid, vid)

    cid = c.get(f"{b}/cuts").json()[0]["id"]
    c.post(f"{b}/cuts/{cid}/items", json={"topic_id": tids[0]})
    c.post(f"{b}/cuts/{cid}/items", json={"start_s": 1, "end_s": 5})
    c.patch(f"{b}/topics/{tids[1]}/triage", json={"state": "maybe"})
    c.patch(f"{b}/cuts/{cid}", json={"name": "Renders?"})
    c.get(f"{b}/cuts/{cid}")
    assert q.calls == []


def test_cut_routes_never_leak_across_projects(tmp_path):
    db = _db(tmp_path)
    pid_a, vid_a, tids_a = _seed(db, video_id="vidaaaa00001", project="A")
    with db.session() as s:
        pid_b = crepo.create_project(s, "B").id
    c = _client(db)
    other = f"/api/projects/{pid_b}/lives/{vid_a}"

    # A live reached from the wrong project is a 404 on every cut route.
    assert c.get(f"{other}/cuts").status_code == 404
    assert c.post(f"{other}/cuts", json={}).status_code == 404
    assert c.get(f"{other}/cuts/1").status_code == 404
    assert c.post(f"{other}/cuts/1/items", json={"topic_id": tids_a[0]}).status_code == 404
    assert c.patch(f"{other}/topics/{tids_a[0]}/triage", json={"state": "maybe"}).status_code == 404


def test_delete_cut_then_reget_recreates_default(tmp_path):
    db = _db(tmp_path)
    pid, vid, _ = _seed(db)
    c = _client(db)
    b = _base(pid, vid)

    cid = c.get(f"{b}/cuts").json()[0]["id"]
    # Compose it, then delete the whole cut.
    tid = c.get(f"{b}/cuts/{cid}").json()["topics"][0]["id"]
    c.post(f"{b}/cuts/{cid}/items", json={"topic_id": tid})
    assert c.delete(f"{b}/cuts/{cid}").status_code == 204
    # The live's last cut is gone; the next GET lazily recreates an EMPTY Cut 1
    # (a fresh default, not the deleted composition — its item is gone).
    cuts = c.get(f"{b}/cuts").json()
    assert len(cuts) == 1 and cuts[0]["name"] == "Cut 1" and cuts[0]["item_count"] == 0
