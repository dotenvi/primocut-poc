"""Tests for syntactic title attributes + their content-hash-gated persistence."""

from __future__ import annotations

from primocut.db import competitors as crepo
from primocut.db.models import VideoAttributesRow
from primocut.db.session import Database
from primocut.metrics.attributes import (
    SOURCE,
    refresh_syntactic_attributes,
    syntactic_attributes,
    title_content_hash,
)


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def test_syntactic_features():
    a = syntactic_attributes('I TRIED this for 30 DAYS?! [Ep. 2] 🔥')
    assert a["has_question"] and a["has_exclamation"]
    assert a["has_digit"] and a["has_brackets_or_pipe"] and a["has_emoji"]
    assert a["caps_heavy"] is False  # mixed case overall
    assert a["length"] == len('I TRIED this for 30 DAYS?! [Ep. 2] 🔥')


def test_caps_heavy_detection():
    assert syntactic_attributes("THIS IS ALL CAPS NEWS")["caps_heavy"] is True
    assert syntactic_attributes("a normal lowercase title")["caps_heavy"] is False


def test_content_hash_stable_and_title_sensitive():
    assert title_content_hash("Same") == title_content_hash("Same")
    assert title_content_hash("A") != title_content_hash("B")


def test_refresh_persists_and_is_idempotent(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCc")
        crepo.upsert_video(s, video_id="v1", channel_id="UCc", title="Original title?")

    with db.session() as s:
        assert refresh_syntactic_attributes(s, ["v1"]) == 1
    with db.session() as s:
        row = crepo.get_video_attributes(s, "v1", SOURCE)
        assert row.attributes["has_question"] is True

    # Re-running with the same title is a no-op (content hash unchanged).
    with db.session() as s:
        assert refresh_syntactic_attributes(s, ["v1"]) == 0

    # Changing the title triggers a single-row recompute.
    with db.session() as s:
        crepo.update_video_packaging(s, "v1", title="No question now")
    with db.session() as s:
        assert refresh_syntactic_attributes(s, ["v1"]) == 1
    with db.session() as s:
        from sqlalchemy import select

        rows = list(s.execute(
            select(VideoAttributesRow).where(VideoAttributesRow.video_id == "v1")
        ).scalars())
        assert len(rows) == 1  # upserted, not duplicated
        assert rows[0].attributes["has_question"] is False
