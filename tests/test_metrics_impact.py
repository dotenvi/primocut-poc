"""Tests for the packaging-impact backfill (the one persisted derived figure)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics.impact import backfill_packaging_impact

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS = Settings()


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed(db, video_id, detected_at, snaps):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id="UCc")
        crepo.upsert_video(s, video_id=video_id, channel_id="UCc")
        for captured_at, views in snaps:
            crepo.add_video_snapshot(s, video_id=video_id, captured_at=captured_at,
                                     views=views, likes=0, comments=0)
        crepo.add_packaging_change(s, video_id=video_id, field="title",
                                   old_value="a", new_value="b", detected_at=detected_at)


def test_backfill_fills_before_and_after_when_elapsed(tmp_path):
    db = _db(tmp_path)
    detected = NOW - timedelta(days=8)  # 7-day after-window has fully elapsed
    _seed(db, "v1", detected, [
        (detected - timedelta(days=7), 0),
        (detected, 700),                       # before ≈ 4.17 views/h
        (detected + timedelta(days=7), 4900),  # after = 4200/7d = 25 views/h
    ])

    with db.session() as s:
        assert backfill_packaging_impact(s, now=NOW, settings=SETTINGS) == 1
    with db.session() as s:
        change = crepo.list_packaging_changes(s, video_id="v1")[0]
        assert change.vph_before is not None and change.vph_after is not None
        assert change.vph_after > change.vph_before

    # Idempotent: a second run fills nothing more.
    with db.session() as s:
        assert backfill_packaging_impact(s, now=NOW, settings=SETTINGS) == 0


def test_backfill_leaves_pending_until_window_elapses(tmp_path):
    db = _db(tmp_path)
    detected = NOW - timedelta(days=2)  # only 2 of 7 days elapsed
    _seed(db, "v1", detected, [(detected - timedelta(days=7), 0), (detected, 700), (NOW, 900)])

    with db.session() as s:
        assert backfill_packaging_impact(s, now=NOW, settings=SETTINGS) == 0
    with db.session() as s:
        change = crepo.list_packaging_changes(s, video_id="v1")[0]
        assert change.vph_after is None
