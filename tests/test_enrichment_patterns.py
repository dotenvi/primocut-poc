"""The AI attributes must reach the per-project conditional distributions under the
SAME guards as the syntactic ones (guideline 10).

The load-bearing property here is that each source is measured against ITS OWN
population: a video nobody enriched is *unknown* for the AI attributes, not
"attribute absent". Counting it as absent would deflate the population rate and
fabricate lift.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics import service
from primocut.metrics.attributes import refresh_syntactic_attributes
from primocut.metrics.enrichment import SOURCE_THUMBNAIL, derive_thumbnail_facets
from primocut.metrics.patterns import conditional_patterns

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
# The uniform fixtures (outliers at 5000 over a 1000 baseline) are calibrated to
# the fixed-multiple bands; the percentile default is covered elsewhere.
SETTINGS = Settings(youtube_api_key="K", band_basis="fixed_multiple")
Z = 1.96


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _raw(expression: str) -> dict:
    return {
        "face_count": 2, "dominant_expression": expression, "has_text": True,
        "text_word_count": 5, "text": "TEXT", "dominant_colors": ["red"],
        "contrast": "high", "has_arrow": False, "has_circle_or_highlight": False,
        "is_confrontation_framing": False,
    }


def _video(db, cid, vid, *, views, days_ago=30, title="A plain title"):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}")
        crepo.upsert_video(s, video_id=vid, channel_id=cid, title=title,
                           published_at=NOW - timedelta(days=days_ago))
        crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=views,
                                 likes=0, comments=0)


def _enrich(db, vid, expression: str):
    """Persist a thumbnail_llm row the way the worker does (raw + derived facets)."""
    raw = _raw(expression)
    with db.session() as s:
        crepo.upsert_video_attributes(
            s, video_id=vid, source=SOURCE_THUMBNAIL,
            attributes={**raw, **derive_thumbnail_facets(raw)},
            content_hash=f"hash-{vid}",
        )


def _seed_project(db, *, n_outliers=20, n_baseline=40):
    """A project whose `n_outliers` videos score 5x over an `n_baseline` median."""
    for i in range(n_outliers):
        _video(db, "UCp", f"out{i}", views=5000, days_ago=20 + i)
    for i in range(n_baseline):
        _video(db, "UCp", f"base{i}", views=1000, days_ago=60 + i)
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCp")
        return proj.id


def _pattern(dash, attribute, fmt="long"):
    # Patterns are reported per format; every fixture here seeds long-form.
    return next((p for p in dash["patterns"][fmt] if p["attribute"] == attribute), None)


# ------------------------- unit: the source tag -------------------------


def test_patterns_carry_their_source():
    # Shock in every outlier vs 40% of the control — separates, tagged by source.
    outliers = [{"thumb_expression_shock": True} for _ in range(20)]
    control = [{"thumb_expression_shock": i < 16} for i in range(40)]

    reported = conditional_patterns(outliers, control, z=Z, source=SOURCE_THUMBNAIL)

    assert [p.source for p in reported] == [SOURCE_THUMBNAIL]
    assert reported[0].verdict == "positive" and reported[0].lift == 2.5


# ------------------------- integration: per-source groups -------------------------


def test_ai_attributes_reach_the_distributions_with_an_honest_control(tmp_path):
    db = _db(tmp_path)
    pid = _seed_project(db)
    # Every OUTLIER is enriched and shows a shocked face; the 40 baseline (control)
    # videos are enriched too, but only 10 of them show one.
    for i in range(20):
        _enrich(db, f"out{i}", "shock")
    for i in range(40):
        _enrich(db, f"base{i}", "shock" if i < 10 else "neutral")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    p = _pattern(dash, "thumb_expression_shock")
    assert p is not None and p["source"] == SOURCE_THUMBNAIL
    assert p["verdict"] == "positive"
    assert p["n_outlier"] == 20 and p["n_control"] == 40
    assert p["freq_outlier"] == 1.0          # 20/20 outliers
    assert p["freq_control"] == 0.25         # 10/40 control
    assert p["lift"] == 4.0


def test_unenriched_control_yields_no_ai_pattern(tmp_path):
    """Only the 20 outliers are enriched; the control videos are not. An un-enriched
    video is *unknown* for `thumbnail_llm`, not "no shocked face" — so the control
    group for that source is empty and no honest comparison exists. The attribute is
    withheld rather than compared against a fabricated (self-referential) baseline.
    (This is why enriching only the outliers now surfaces no AI pattern: the control
    must be enriched too.)"""
    db = _db(tmp_path)
    pid = _seed_project(db)
    for i in range(20):
        _enrich(db, f"out{i}", "shock")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    assert _pattern(dash, "thumb_expression_shock") is None


def test_sparse_enrichment_is_self_regulated_by_the_wilson_guard(tmp_path):
    """Enrichment is opt-in and sparse. With only a handful of enriched videos on
    each side, the Wilson intervals are wide and overlap, so nothing is reported —
    no arbitrary minimum-occurrence floor is needed."""
    db = _db(tmp_path)
    pid = _seed_project(db)
    for i in range(8):
        _enrich(db, f"out{i}", "shock")
    for i in range(8):  # a small, mixed control
        _enrich(db, f"base{i}", "shock" if i < 4 else "neutral")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    assert _pattern(dash, "thumb_expression_shock") is None


def test_syntactic_and_ai_patterns_coexist(tmp_path):
    db = _db(tmp_path)
    for i in range(20):
        _video(db, "UCp", f"out{i}", views=5000, days_ago=20 + i, title=f"Will it work? {i}")
    for i in range(40):
        _video(db, "UCp", f"base{i}", views=1000, days_ago=60 + i, title="plain title")
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCp")
        pid = proj.id
        refresh_syntactic_attributes(
            s, [v.video_id for v in crepo.list_channel_videos(s, "UCp")]
        )
    # Enrich the outliers (all shock) and the control (few shock) so the AI source
    # has both groups; the syntactic "?" comes for free on every video.
    for i in range(20):
        _enrich(db, f"out{i}", "shock")
    for i in range(40):
        _enrich(db, f"base{i}", "shock" if i < 8 else "neutral")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    syntactic = _pattern(dash, "has_question")
    ai = _pattern(dash, "thumb_expression_shock")
    assert syntactic is not None and syntactic["source"] == "syntactic"
    assert ai is not None and ai["source"] == SOURCE_THUMBNAIL
