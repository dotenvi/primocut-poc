"""Phase 3 AI packaging: the deterministic INPUT builder (no LLM).

`build_packaging_context` reads the LIVE's project (Phase 2 patterns) under three
lenses the packaging prompt cites:

- differential  — the conditional-distribution patterns (Wilson-gated) that
  separate outliers from control;
- conventions   — booleans near-universal across the WHOLE niche (a requirement,
  not a differential: their intervals overlap high, so the differential guard
  drops them, yet they are what the niche requires);
- carries_value — recurring non-boolean values (the "name that carries") that
  appear disproportionately in outliers, guarded by a min-videos + min-lift floor
  so a value seen once is never reported.

Plus real outlier examples (titles + enriched attributes) and a `flat` flag for a
niche where packaging is not the lever. No LLM here — pure reads over the metrics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics import packaging_signals as ps
from primocut.metrics.enrichment import (
    SOURCE_THUMBNAIL,
    SOURCE_TITLE,
    derive_thumbnail_facets,
    derive_title_facets,
)

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
# Fixed-multiple bands so the 5000/1000 fixtures land cleanly in the tail; the
# percentile default is exercised in the metrics tests.
SETTINGS = Settings(youtube_api_key="K", band_basis="fixed_multiple")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _video(db, cid, vid, *, views, days_ago=30, title="A plain title"):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}")
        crepo.upsert_video(s, video_id=vid, channel_id=cid, title=title,
                           published_at=NOW - timedelta(days=days_ago))
        crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=views,
                                 likes=0, comments=0)


def _thumb(expression="neutral", has_text=False):
    raw = {
        "face_count": 1, "dominant_expression": expression, "has_text": has_text,
        "text_word_count": 3 if has_text else 0, "text": "GO" if has_text else "",
        "dominant_colors": ["red"], "contrast": "high", "has_arrow": False,
        "has_circle_or_highlight": False, "is_confrontation_framing": False,
    }
    return raw


def _title_raw(names, *, tone="neutral", quoted=""):
    return {"person_names": list(names), "topic": "x", "tone": tone,
            "quoted_speech": quoted}


def _enrich_thumb(db, vid, **kw):
    raw = _thumb(**kw)
    with db.session() as s:
        crepo.upsert_video_attributes(
            s, video_id=vid, source=SOURCE_THUMBNAIL,
            attributes={**raw, **derive_thumbnail_facets(raw)},
            content_hash=f"th-{vid}")


def _enrich_title(db, vid, names, **kw):
    raw = _title_raw(names, **kw)
    with db.session() as s:
        crepo.upsert_video_attributes(
            s, video_id=vid, source=SOURCE_TITLE,
            attributes={**raw, **derive_title_facets(raw)},
            content_hash=f"ti-{vid}")


def _seed(db, *, n_out=20, n_base=40, out_title="Out", base_title="plain"):
    for i in range(n_out):
        _video(db, "UCp", f"out{i}", views=5000, days_ago=20 + i,
               title=f"{out_title} {i}")
    for i in range(n_base):
        _video(db, "UCp", f"base{i}", views=1000, days_ago=60 + i,
               title=f"{base_title} {i}")
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCp")
        return proj.id


def _ctx(db, pid):
    with db.session() as s:
        return ps.build_packaging_context(s, pid, fmt="long", settings=SETTINGS, now=NOW)


# ------------------------- missing project -------------------------


def test_missing_project_returns_none(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        assert ps.build_packaging_context(s, 999, settings=SETTINGS, now=NOW) is None


# ------------------------- differential -------------------------


def test_differential_reports_a_separating_attribute(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    # Shock in every outlier vs a quarter of the control → a positive differential.
    for i in range(20):
        _enrich_thumb(db, f"out{i}", expression="shock")
    for i in range(40):
        _enrich_thumb(db, f"base{i}", expression="shock" if i < 10 else "neutral")

    ctx = _ctx(db, pid)
    diff = {d["attribute"]: d for d in ctx["differential"]}
    assert "thumb_expression_shock" in diff
    assert diff["thumb_expression_shock"]["verdict"] == "positive"
    assert diff["thumb_expression_shock"]["source"] == SOURCE_THUMBNAIL
    assert not ctx["flat"]


# ------------------------- conventions -------------------------


def test_convention_is_a_near_universal_boolean_not_a_differential(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    # EVERY mature video (outlier and control) carries overlaid text: a niche
    # requirement. It never separates the two groups, so it is a convention, not a
    # differential.
    for i in range(20):
        _enrich_thumb(db, f"out{i}", has_text=True)
    for i in range(40):
        _enrich_thumb(db, f"base{i}", has_text=True)

    ctx = _ctx(db, pid)
    conv = {c["attribute"]: c for c in ctx["conventions"]}
    assert "thumb_has_text" in conv
    assert conv["thumb_has_text"]["freq"] == 1.0
    # A convention is not double-reported as a differential.
    assert all(d["attribute"] != "thumb_has_text" for d in ctx["differential"])


# ------------------------- carries value (the name that carries) -------------------------


def test_carries_value_reports_a_name_that_carries(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    # "Alpha" carries: 15/20 outliers vs 4/40 control. "Rare" appears in a single
    # outlier and must stay below the min-videos floor.
    for i in range(20):
        names = ["Alpha"] if i < 15 else (["Rare"] if i == 15 else [])
        _enrich_title(db, f"out{i}", names)
    for i in range(40):
        _enrich_title(db, f"base{i}", ["Alpha"] if i < 4 else [])

    ctx = _ctx(db, pid)
    names = {n["value"].lower(): n for n in ctx["carries_value"]["person_names"]}
    assert "alpha" in names
    assert names["alpha"]["videos_outlier"] == 15
    assert names["alpha"]["lift"] is not None and names["alpha"]["lift"] > 1.5
    assert "rare" not in names  # a single-video value is never a pattern
    assert not ctx["flat"]


# ------------------------- examples -------------------------


def test_examples_carry_real_titles_and_attributes(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db, out_title="Winner")
    for i in range(20):
        _enrich_title(db, f"out{i}", ["Alpha"], quoted="say it")

    ctx = _ctx(db, pid)
    assert ctx["examples"], "expected real outlier examples"
    ex = ctx["examples"][0]
    assert ex["title"].startswith("Winner")
    assert ex["band"] is not None
    # The enriched title attributes ride along for the prompt to cite.
    assert ex["title_attrs"]["person_names"] == ["Alpha"]


# ------------------------- flat niche -------------------------


def test_flat_niche_when_nothing_separates(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    # Enrich uniformly: same attributes in outliers and control, no names → nothing
    # separates and nothing carries. Packaging is not the lever here.
    for i in range(20):
        _enrich_thumb(db, f"out{i}", expression="neutral")
        _enrich_title(db, f"out{i}", [])
    for i in range(40):
        _enrich_thumb(db, f"base{i}", expression="neutral")
        _enrich_title(db, f"base{i}", [])

    ctx = _ctx(db, pid)
    assert ctx["differential"] == []
    assert all(v == [] for v in ctx["carries_value"].values())
    assert ctx["flat"] is True


def test_no_enrichment_degrades_to_generic_not_invented(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)  # no attributes enriched at all
    ctx = _ctx(db, pid)
    assert ctx["differential"] == []
    assert ctx["conventions"] == []
    assert all(v == [] for v in ctx["carries_value"].values())
    assert ctx["flat"] is True
    assert ctx["niche"]["outliers"] > 0  # the niche still has measured outliers
