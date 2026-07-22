"""Phase 3 AI packaging worker jobs (LLM faked, no network/GPU).

Covers: title/brief generation building the prompt from the LIVE's project patterns
(a channel in two projects → the live project's patterns), K respected end to end,
per-item isolation, the pay-once cache + explicit regeneration, deterministic frame
extraction (NO LLM), the ranking gated behind an explicit trigger, and the audience
match cached per live + degrading with no syntheses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db import cuts as cuts_repo
from primocut.db import packaging as pkg_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Segment, Topic, Transcript, Video, VideoStatus
from primocut.metrics.enrichment import SOURCE_THUMBNAIL, derive_thumbnail_facets
from primocut.worker import packaging_jobs as jobs

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


class FakeLLM:
    def __init__(self, response_fn):
        self.response_fn = response_fn
        self.calls = []

    def select(self, *, system, user, tool, tool_name, images=None, model=None):
        self.calls.append({"tool_name": tool_name, "user": user})
        return self.response_fn(tool_name, user)


def _thumb_raw(expression):
    return {"face_count": 1, "dominant_expression": expression, "has_text": True,
            "text_word_count": 3, "text": "GO", "dominant_colors": ["red"],
            "contrast": "high", "has_arrow": False, "has_circle_or_highlight": False,
            "is_confrontation_framing": False}


def _seed_niche(db, project_name, channel_id, marker):
    """A project + a channel with 20 outliers (all shock) + 40 control. The outlier
    titles carry `marker` so we can prove which project's patterns reached the prompt."""
    with db.session() as s:
        proj = crepo.create_project(s, project_name)
        crepo.upsert_channel(s, channel_id=channel_id, uploads_playlist_id=f"UU{channel_id}")
        crepo.link_channel(s, proj.id, channel_id)
        for i in range(20):
            vid = f"{channel_id}o{i}"
            crepo.upsert_video(s, video_id=vid, channel_id=channel_id,
                               title=f"{marker} outlier {i}",
                               published_at=NOW - timedelta(days=20 + i))
            crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=5000,
                                     likes=0, comments=0)
        for i in range(40):
            vid = f"{channel_id}b{i}"
            crepo.upsert_video(s, video_id=vid, channel_id=channel_id, title="plain",
                               published_at=NOW - timedelta(days=60 + i))
            crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=1000,
                                     likes=0, comments=0)
    # Enrich thumbnails so a differential exists: outliers shock, control neutral.
    for i in range(20):
        _enrich_thumb(db, f"{channel_id}o{i}", "shock")
    for i in range(40):
        _enrich_thumb(db, f"{channel_id}b{i}", "shock" if i < 10 else "neutral")
    return proj.id


def _enrich_thumb(db, vid, expression):
    raw = _thumb_raw(expression)
    with db.session() as s:
        crepo.upsert_video_attributes(s, video_id=vid, source=SOURCE_THUMBNAIL,
                                      attributes={**raw, **derive_thumbnail_facets(raw)},
                                      content_hash=f"th-{vid}")


def _live(db, pid, video_id="live0001", n_items=2):
    with db.session() as s:
        repo.upsert_video(s, Video(id=video_id, url=f"https://y/{video_id}",
                                   title="Live", channel="C", duration=600.0,
                                   status=VideoStatus.DONE, project_id=pid))
        repo.save_transcript(s, Transcript(video_id=video_id, language="pt", model="m",
            segments=[Segment(index=i, start=float(i * 100), end=float(i * 100 + 90),
                              text=f"conteudo do topico {i}") for i in range(n_items)]))
        repo.save_topics(s, video_id, [
            Topic(title=f"Topic {i}", summary="s", start=float(i * 100),
                  end=float(i * 100 + 60), score=0.5) for i in range(n_items)])
        cut = cuts_repo.ensure_default_cut(s, video_id)
        for t in repo.get_topic_rows(s, video_id):
            cuts_repo.add_item(s, cut, duration=600.0, topic_id=t.id)
        item_ids = [it.id for it in cuts_repo.list_items(s, cut.id)]
        return video_id, cut.id, item_ids


# ------------------------- title generation -------------------------


def test_titles_prompt_uses_the_live_projects_patterns(tmp_path):
    """A channel is tracked in two projects; the LIVE belongs to project A. The
    title prompt must carry A's outlier titles ('AAA'), never B's ('BBB')."""
    db = _db(tmp_path)
    settings = Settings(youtube_api_key="K", band_basis="fixed_multiple")
    pid_a = _seed_niche(db, "A", "UCa", "AAA")
    _seed_niche(db, "B", "UCb", "BBB")
    video_id, cut_id, item_ids = _live(db, pid_a)

    seen_prompts = []

    def respond(tool_name, user):
        seen_prompts.append(user)
        return {"titles": [
            {"title": f"Proposta {i}", "length_chars": 5,
             "patterns_applied": [{"kind": "differential", "reference": "D1"}],
             "rationale": "r"} for i in range(5)]}

    llm = FakeLLM(respond)
    out = jobs.generate_cut_packaging(video_id, cut_id, "title", k=5, llm=llm,
                                      settings=settings, database=db)

    assert out["generated"] == 2  # one pass per item
    joined = "\n".join(seen_prompts)
    assert "AAA" in joined and "BBB" not in joined  # the live project's patterns only

    with db.session() as s:
        rows = pkg_repo.packaging_by_item(s, cut_id)
        assert len(rows[item_ids[0]]["title"].payload["titles"]) == 5  # K respected


def test_titles_pay_once_then_regenerate(tmp_path):
    db = _db(tmp_path)
    settings = Settings(youtube_api_key="K", band_basis="fixed_multiple")
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=1)

    calls = {"n": 0}

    def respond(tool_name, user):
        calls["n"] += 1
        return {"titles": [{"title": "T", "length_chars": 1, "patterns_applied": [],
                            "rationale": ""}]}

    llm = FakeLLM(respond)
    jobs.generate_cut_packaging(video_id, cut_id, "title", k=1, llm=llm,
                                settings=settings, database=db)
    # Second identical run is served from the cache — no LLM call.
    r2 = jobs.generate_cut_packaging(video_id, cut_id, "title", k=1, llm=llm,
                                     settings=settings, database=db)
    assert r2["skipped"] == 1 and calls["n"] == 1
    # Explicit regeneration re-runs.
    r3 = jobs.generate_cut_packaging(video_id, cut_id, "title", k=1, llm=llm,
                                     settings=settings, database=db, regenerate=True)
    assert r3["generated"] == 1 and calls["n"] == 2


def test_title_generation_isolates_a_failing_item(tmp_path):
    db = _db(tmp_path)
    settings = Settings(youtube_api_key="K", band_basis="fixed_multiple")
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=2)

    def respond(tool_name, user):
        if "topico 0" in user:  # the first item's excerpt → boom
            raise RuntimeError("model exploded")
        return {"titles": [{"title": "ok", "length_chars": 2, "patterns_applied": [],
                            "rationale": ""}]}

    out = jobs.generate_cut_packaging(video_id, cut_id, "title", k=1,
                                      llm=FakeLLM(respond), settings=settings, database=db)
    assert out["generated"] == 1 and out["failed"] == 1
    with db.session() as s:
        by_item = pkg_repo.packaging_by_item(s, cut_id)
        assert by_item[item_ids[0]]["title"].state == "error"
        assert by_item[item_ids[1]]["title"].state == "done"


# ------------------------- frame extraction (no LLM) -------------------------


def test_frame_extraction_takes_no_llm(tmp_path):
    db = _db(tmp_path)
    settings = Settings(clips_dir=str(tmp_path / "clips"))
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=1)
    # Mark the item's clip as rendered on disk.
    from primocut.db import clips as clips_repo

    rel = f"{video_id}/{cut_id}/001-topic.mp4"
    clip_file = Path(settings.clips_dir) / rel
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"x")
    with db.session() as s:
        clips_repo.reset_to_queued(s, cut_item_id=item_ids[0], video_id=video_id,
                                   cut_id=cut_id, position=0, filename="001-topic.mp4",
                                   rel_path=rel, phash="p", padding_s=0.0,
                                   profile="nvenc", start_s=0.0, end_s=60.0)
        clips_repo.set_state(s, clips_repo.clip_for_item(s, item_ids[0]).id, "done")

    captured = []

    def fake_extract(clip_path, out_dir, *, count, scene_threshold, regenerate=False):
        captured.append((clip_path, count))
        out_dir.mkdir(parents=True, exist_ok=True)
        names = [f"frame-{i + 1:03d}.jpg" for i in range(count)]
        for n in names:
            (out_dir / n).write_bytes(b"\xff\xd8\xff")
        return names

    # No llm argument exists on this job at all.
    out = jobs.extract_cut_frames(video_id, cut_id, count=3, settings=settings,
                                  database=db, extract_fn=fake_extract)
    assert out["extracted"] == 1
    assert captured[0][1] == 3
    fdir = jobs.frames_dir_for(settings, video_id, cut_id, 0)
    assert sorted(p.name for p in fdir.glob("frame-*.jpg")) == \
        ["frame-001.jpg", "frame-002.jpg", "frame-003.jpg"]


def test_frame_ranking_requires_explicit_trigger_and_ranks(tmp_path):
    db = _db(tmp_path)
    settings = Settings(clips_dir=str(tmp_path / "clips"), youtube_api_key="K",
                        band_basis="fixed_multiple")
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=1)
    # Extraction (deterministic) never triggers ranking; only the explicit job does.
    fdir = jobs.frames_dir_for(settings, video_id, cut_id, 0)
    fdir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (fdir / f"frame-{i + 1:03d}.jpg").write_bytes(b"\xff\xd8\xff")

    def respond(tool_name, user):
        assert tool_name == "rank_frames"
        return {"rankings": [{"frame": 2, "score": 0.9, "expression": 0.8,
                              "sharpness": 0.7, "framing": 0.6, "fits_brief": True,
                              "note": "n"}], "best_frame": 2}

    llm = FakeLLM(respond)
    out = jobs.rank_cut_frames(video_id, cut_id, llm=llm, settings=settings, database=db)
    assert out["ranked"] == 1 and len(llm.calls) == 1
    with db.session() as s:
        row = pkg_repo.get_packaging(s, item_ids[0], "frame_ranking")
        assert row.payload["best_filename"] == "frame-003.jpg"


# ------------------------- audience match -------------------------


def test_audience_match_degrades_without_syntheses(tmp_path):
    db = _db(tmp_path)
    settings = Settings(youtube_api_key="K", band_basis="fixed_multiple")
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=1)

    llm = FakeLLM(lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    out = jobs.match_live_audience(video_id, llm=llm, settings=settings, database=db)
    assert out["matched"] == 0
    with db.session() as s:
        row = pkg_repo.get_audience_match(s, video_id)
        assert row.payload == {}  # empty, cached, no LLM


def test_audience_match_caches_per_live(tmp_path):
    db = _db(tmp_path)
    settings = Settings(youtube_api_key="K", band_basis="fixed_multiple")
    pid = _seed_niche(db, "A", "UCa", "AAA")
    video_id, cut_id, item_ids = _live(db, pid, n_items=1)
    # Give the niche one synthesis with a request so the match runs.
    with db.session() as s:
        crepo.upsert_video_enrichment(
            s, video_id="UCao0", kind="comment_synthesis",
            payload={"requests": [{"type": "guest", "value": "Fulano", "mentions": 3}]},
            content_hash="c1")

    calls = {"n": 0}

    def respond(tool_name, user):
        calls["n"] += 1
        return {"matches": [{"topic_id": None, "request_type": "guest",
                             "request_value": "Fulano", "confidence": "weak"}]}

    # topic_id None gets dropped, but the pass still ran and cached.
    with db.session() as s:
        tid = repo.get_topic_rows(s, video_id)[0].id

    def respond2(tool_name, user):
        calls["n"] += 1
        return {"matches": [{"topic_id": tid, "request_type": "guest",
                             "request_value": "Fulano", "confidence": "strong"}]}

    jobs.match_live_audience(video_id, llm=FakeLLM(respond2), settings=settings, database=db)
    r2 = jobs.match_live_audience(video_id, llm=FakeLLM(respond2), settings=settings,
                                 database=db)
    assert r2["state"] == "cached" and calls["n"] == 1
    with db.session() as s:
        row = pkg_repo.get_audience_match(s, video_id)
        assert str(tid) in row.payload
