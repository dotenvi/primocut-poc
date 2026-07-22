"""DAO tests for the Phase 3 decision bench (cuts, items, triage).

Focus on the model rules: the two-concept separation (triage per live vs `in` per
active cut), the 4-precedence derived state, transactional reorder and range
validation. No network/GPU (pure SQLite).
"""

from __future__ import annotations

from primocut.db import cuts as cuts_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed(s, video_id="vid00000001", duration=600.0, n_topics=3):
    """A project, a live and `n_topics` topics; returns the topic row ids."""
    from primocut.db import competitors as crepo

    pid = crepo.create_project(s, "N").id
    repo.upsert_video(s, Video(
        id=video_id, url=f"https://y/{video_id}", title="Live", channel="C",
        duration=duration, status=VideoStatus.DONE, project_id=pid,
    ))
    topics = [
        Topic(title=f"T{i}", summary="", start=float(i * 100),
              end=float(i * 100 + 60), score=0.5)
        for i in range(n_topics)
    ]
    repo.save_topics(s, video_id, topics)
    return pid, video_id, [t.id for t in repo.get_topic_rows(s, video_id)]


def test_default_cut_created_lazily_and_once(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, _ = _seed(s)
        c1 = cuts_repo.ensure_default_cut(s, vid)
        assert c1.name == "Cut 1"
        c2 = cuts_repo.ensure_default_cut(s, vid)
        assert c2.id == c1.id  # idempotent, not a second cut
        assert len(cuts_repo.list_cuts(s, vid)) == 1


def test_triage_persists_and_open_clears(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s)
        cuts_repo.set_triage(s, vid, tids[0], "maybe")
        cuts_repo.set_triage(s, vid, tids[1], "discarded")
    # Survives a new session (persisted across "sessions").
    with db.session() as s:
        assert cuts_repo.triage_map(s, vid) == {tids[0]: "maybe", tids[1]: "discarded"}
        cuts_repo.set_triage(s, vid, tids[0], "open")
    with db.session() as s:
        assert cuts_repo.triage_map(s, vid) == {tids[1]: "discarded"}


def test_triage_rejects_in_and_unknown_state(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s)
        for bad in ("in", "bogus"):
            try:
                cuts_repo.set_triage(s, vid, tids[0], bad)
                assert False, f"expected ValueError for {bad!r}"
            except ValueError:
                pass


def test_derived_state_four_precedences(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s, n_topics=4)
        cut = cuts_repo.ensure_default_cut(s, vid)
        # tids[0] -> in (a cut item); tids[1] -> maybe; tids[2] -> discarded;
        # tids[3] -> open (no row anywhere).
        cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[0])
        cuts_repo.set_triage(s, vid, tids[1], "maybe")
        cuts_repo.set_triage(s, vid, tids[2], "discarded")
        # Even if a topic is BOTH in the cut and has a triage row, `in` wins.
        cuts_repo.set_triage(s, vid, tids[0], "maybe")

        in_cut = cuts_repo.cut_topic_ids(s, cut.id)
        triage = cuts_repo.triage_map(s, vid)
        assert cuts_repo.derive_state(tids[0], in_cut, triage) == "in"
        assert cuts_repo.derive_state(tids[1], in_cut, triage) == "maybe"
        assert cuts_repo.derive_state(tids[2], in_cut, triage) == "discarded"
        assert cuts_repo.derive_state(tids[3], in_cut, triage) == "open"


def test_in_never_written_to_triage(tmp_path):
    """Membership is cut_items only; adding an item writes no triage row."""
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s)
        cut = cuts_repo.ensure_default_cut(s, vid)
        cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[0])
        assert cuts_repo.triage_map(s, vid) == {}  # no "in" leaked into triage
        assert cuts_repo.cut_topic_ids(s, cut.id) == {tids[0]}


def test_triage_shared_across_cuts_membership_is_per_cut(tmp_path):
    """Two cuts on one live: a topic `in` cut A is only `maybe` globally, so cut B
    reads it as `maybe` while cut A reads `in`."""
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s)
        cut_a = cuts_repo.ensure_default_cut(s, vid)
        cut_b = cuts_repo.create_cut(s, vid, "Cut 2")
        cuts_repo.set_triage(s, vid, tids[0], "maybe")   # live-scoped opinion
        cuts_repo.add_item(s, cut_a, duration=600.0, topic_id=tids[0])  # in A only

        triage = cuts_repo.triage_map(s, vid)
        in_a = cuts_repo.cut_topic_ids(s, cut_a.id)
        in_b = cuts_repo.cut_topic_ids(s, cut_b.id)
        assert cuts_repo.derive_state(tids[0], in_a, triage) == "in"
        assert cuts_repo.derive_state(tids[0], in_b, triage) == "maybe"


def test_topic_item_snapshots_range_and_is_idempotent(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s)
        cut = cuts_repo.ensure_default_cut(s, vid)
        it1 = cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[1])
        assert (it1.start_s, it1.end_s) == (100.0, 160.0)  # snapshotted from topic
        it2 = cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[1])
        assert it2.id == it1.id  # same topic added twice -> one item
        assert len(cuts_repo.list_items(s, cut.id)) == 1


def test_manual_range_validation(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, _ = _seed(s, duration=600.0)
        cut = cuts_repo.ensure_default_cut(s, vid)
        # Valid manual range.
        it = cuts_repo.add_item(s, cut, duration=600.0, start_s=10.0, end_s=50.0)
        assert it.topic_id is None and (it.start_s, it.end_s) == (10.0, 50.0)
        # end <= start, and beyond the live duration -> rejected.
        for bad in ((50.0, 50.0), (50.0, 10.0), (500.0, 700.0)):
            try:
                cuts_repo.add_item(s, cut, duration=600.0, start_s=bad[0], end_s=bad[1])
                assert False, f"expected ValueError for {bad}"
            except ValueError:
                pass


def test_reorder_is_transactional_permutation(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s, n_topics=3)
        cut = cuts_repo.ensure_default_cut(s, vid)
        ids = [cuts_repo.add_item(s, cut, duration=600.0, topic_id=t).id for t in tids]
        # Reverse the order.
        cuts_repo.reorder_items(s, cut, list(reversed(ids)))
        assert [it.id for it in cuts_repo.list_items(s, cut.id)] == list(reversed(ids))
        assert [it.position for it in cuts_repo.list_items(s, cut.id)] == [0, 1, 2]

        # A non-permutation (missing/extra id) is rejected and changes nothing.
        before = [it.id for it in cuts_repo.list_items(s, cut.id)]
        for bad in ([ids[0], ids[1]], [ids[0], ids[1], 99999]):
            try:
                cuts_repo.reorder_items(s, cut, bad)
                assert False, "expected ValueError"
            except ValueError:
                pass
        assert [it.id for it in cuts_repo.list_items(s, cut.id)] == before


def test_remove_item_renumbers(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s, n_topics=3)
        cut = cuts_repo.ensure_default_cut(s, vid)
        ids = [cuts_repo.add_item(s, cut, duration=600.0, topic_id=t).id for t in tids]
        assert cuts_repo.remove_item(s, cut, ids[0]) is True
        rows = cuts_repo.list_items(s, cut.id)
        assert [it.id for it in rows] == [ids[1], ids[2]]
        assert [it.position for it in rows] == [0, 1]  # contiguous after removal


def test_topic_regeneration_degrades_item_keeps_composition(tmp_path):
    """SET NULL: regenerating topics must not silently drop a composed item; the
    topic item degrades into a preserved manual range (topic_id NULL, range kept),
    while its triage row is cascaded away."""
    db = _db(tmp_path)
    with db.session() as s:
        _, vid, tids = _seed(s, n_topics=2)
        cut = cuts_repo.ensure_default_cut(s, vid)
        cuts_repo.add_item(s, cut, duration=600.0, topic_id=tids[0])
        cuts_repo.set_triage(s, vid, tids[1], "discarded")
    # Regenerate topics (delete + re-insert, exactly like save_topics does).
    with db.session() as s:
        repo.save_topics(s, vid, [Topic(title="New", summary="", start=5.0, end=9.0, score=0.5)])
    with db.session() as s:
        cut = cuts_repo.list_cuts(s, vid)[0]
        items = cuts_repo.list_items(s, cut.id)
        assert len(items) == 1  # item survived
        assert items[0].topic_id is None  # degraded to a manual range
        assert (items[0].start_s, items[0].end_s) == (0.0, 60.0)  # range preserved
        assert cuts_repo.triage_map(s, vid) == {}  # triage cascaded away
