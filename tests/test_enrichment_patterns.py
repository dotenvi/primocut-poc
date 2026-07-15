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
SETTINGS = Settings(youtube_api_key="K")


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


def _pattern(dash, attribute):
    return next((p for p in dash["patterns"] if p["attribute"] == attribute), None)


# ------------------------- unit: the source tag -------------------------


def test_patterns_carry_their_source():
    population = [{"thumb_expression_shock": i < 40} for i in range(100)]
    outliers = [{"thumb_expression_shock": True} for _ in range(20)]

    reported = conditional_patterns(
        population, outliers, min_occurrence=15, source=SOURCE_THUMBNAIL
    )

    assert [p.source for p in reported] == [SOURCE_THUMBNAIL]
    assert reported[0].lift == 2.5


# ------------------------- integration: per-source population -------------------------


def test_ai_attributes_reach_the_distributions_with_an_honest_population(tmp_path):
    db = _db(tmp_path)
    pid = _seed_project(db)
    # Every OUTLIER is enriched and shows a shocked face; the 40 baseline videos
    # are enriched too, but only 10 of them show one.
    for i in range(20):
        _enrich(db, f"out{i}", "shock")
    for i in range(40):
        _enrich(db, f"base{i}", "shock" if i < 10 else "neutral")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    p = _pattern(dash, "thumb_expression_shock")
    assert p is not None and p["source"] == SOURCE_THUMBNAIL
    assert p["outlier_occurrences"] == 20
    assert p["outlier_rate"] == 1.0          # 20/20 outliers
    assert p["population_rate"] == 0.5       # 30/60 videos
    assert p["lift"] == 2.0


def test_unenriched_videos_are_unknown_not_absent(tmp_path):
    """Only the 20 outliers are enriched. The population for `thumbnail_llm` is
    therefore those same 20 videos — NOT all 60 scored ones. Counting the 40
    un-enriched videos as "no shocked face" would report a fabricated 3x lift."""
    db = _db(tmp_path)
    pid = _seed_project(db)
    for i in range(20):
        _enrich(db, f"out{i}", "shock")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    p = _pattern(dash, "thumb_expression_shock")
    assert p is not None
    assert p["population_rate"] == 1.0  # 20/20 enriched — not 20/60
    assert p["lift"] == 1.0             # no evidence either way, honestly reported


def test_sparse_enrichment_is_suppressed_by_the_minimum_occurrence_guard(tmp_path):
    """Enrichment is opt-in and sparse: until enough outliers are enriched, the
    guard must report nothing rather than a small-sample claim."""
    db = _db(tmp_path)
    pid = _seed_project(db)
    for i in range(8):  # below pattern_min_occurrence (15)
        _enrich(db, f"out{i}", "shock")

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
    for i in range(20):
        _enrich(db, f"out{i}", "shock")

    with db.session() as s:
        dash = service.project_dashboard(s, pid, settings=SETTINGS, now=NOW)

    syntactic = _pattern(dash, "has_question")
    ai = _pattern(dash, "thumb_expression_shock")
    assert syntactic is not None and syntactic["source"] == "syntactic"
    assert ai is not None and ai["source"] == SOURCE_THUMBNAIL
