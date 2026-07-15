"""DB-reading orchestration: assemble the three dashboard payloads.

Everything here is a PURE READ — scores are computed live over the snapshots via
`metrics.core`; nothing is written. Per-channel/per-video metrics depend only on
the channel's own data, so a channel shared by two projects yields identical
figures; project dashboards merely scope which channels/videos are ranked and
which population feeds the pattern guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import Settings, get_settings
from ..db import competitors as crepo
from . import core
from .attributes import SOURCE as ATTR_SOURCE
from .patterns import conditional_patterns


@dataclass
class _Scored:
    video_id: str
    channel_id: str
    title: str
    is_short: bool
    views: int
    score: core.OutlierScore


def _now(now: datetime | None) -> datetime:
    """Normalize to a naive UTC datetime. SQLite round-trips drop tzinfo, so every
    datetime read back from the DB is naive; we compare against a naive `now`."""
    ts = now or datetime.now(timezone.utc)
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _snaps(session, video_id: str) -> list[core.Snap]:
    return [
        core.Snap(captured_at=r.captured_at, views=r.views)
        for r in crepo.list_video_snapshots(session, video_id)
    ]


def _format(is_short: bool) -> str:
    return "short" if is_short else "long"


# ------------------------- channel-level outlier scoring -------------------------


def _channel_scores(
    session, channel_id: str, *, settings: Settings, now: datetime
) -> tuple[dict[str, _Scored], dict[str, core.Baseline | None]]:
    """Score every video of a channel against its per-format median baseline.

    The baseline for each format is the median of the same-format videos in the
    rolling window = the smaller of "the last N videos" and "the last D days".
    Returns (video_id -> _Scored, format -> Baseline|None).
    """
    videos = crepo.list_channel_videos(session, channel_id)
    views_by_id = crepo.latest_views(session, [v.video_id for v in videos])
    brackets = core.parse_brackets(settings.outlier_brackets)
    provisional_cut = now - timedelta(days=settings.outlier_provisional_days)
    window_cut = now - timedelta(days=settings.outlier_window_days)

    baselines: dict[str, core.Baseline | None] = {}
    for fmt, is_short in (("long", False), ("short", True)):
        peers = [
            v for v in videos
            if v.is_short == is_short
            and v.published_at is not None
            and v.published_at >= window_cut
            and v.video_id in views_by_id
        ]
        peers.sort(key=lambda v: v.published_at, reverse=True)
        peers = peers[: settings.outlier_window_videos]
        baselines[fmt] = core.channel_baseline(
            [views_by_id[v.video_id] for v in peers], min_sample=settings.outlier_min_sample
        )

    scored: dict[str, _Scored] = {}
    for v in videos:
        if v.video_id not in views_by_id:
            continue  # no snapshot yet — cannot be scored
        provisional = v.published_at is None or v.published_at > provisional_cut
        score = core.outlier_score(
            views_by_id[v.video_id], baselines[_format(v.is_short)],
            brackets=brackets, provisional=provisional,
        )
        scored[v.video_id] = _Scored(
            v.video_id, channel_id, v.title, v.is_short, views_by_id[v.video_id], score
        )
    return scored, baselines


def _is_definitive_outlier(sc: _Scored, *, threshold: float) -> bool:
    return (
        sc.score.status == "ok"
        and sc.score.score is not None
        and sc.score.score >= threshold
    )


def _outlier_payload(sc: _Scored, *, with_channel: bool = False, vph: float | None = None) -> dict:
    out = {
        "video_id": sc.video_id,
        "title": sc.title,
        "is_short": sc.is_short,
        "views": sc.views,
        "score": sc.score.score,
        "bracket": sc.score.bracket,
        "provisional": sc.score.provisional,
    }
    if with_channel:
        out["channel_id"] = sc.channel_id
        out["current_vph"] = vph
    return out


# ------------------------- resurgence / engagement helpers -------------------------


def _resurgence_for(session, video, *, settings: Settings, now: datetime) -> core.Resurgence:
    return core.resurgence(
        _snaps(session, video.video_id),
        published_at=video.published_at, now=now,
        min_age_days=settings.resurgence_min_age_days, k=settings.resurgence_k,
        baseline_days=settings.resurgence_baseline_days, min_vph=settings.resurgence_min_vph,
        window_hours=settings.vph_current_window_hours,
    )


# ------------------------- video report -------------------------


def video_report(session, channel_id, video_id, *, settings=None, now=None) -> dict | None:
    settings = settings or get_settings()
    now = _now(now)
    video = crepo.get_video(session, video_id)
    if video is None or video.channel_id != channel_id:
        return None

    snaps = _snaps(session, video_id)
    scored, _ = _channel_scores(session, channel_id, settings=settings, now=now)
    sc = scored.get(video_id)
    latest = crepo.latest_video_snapshot(session, video_id)
    eng = (
        core.engagement_rates(latest.views, latest.likes, latest.comments)
        if latest is not None else core.Engagement(None, None)
    )
    res = _resurgence_for(session, video, settings=settings, now=now)
    attrs_row = crepo.get_video_attributes(session, video_id, ATTR_SOURCE)
    changes = crepo.list_packaging_changes(session, video_id=video_id)

    outlier = (
        {
            "score": sc.score.score, "bracket": sc.score.bracket,
            "provisional": sc.score.provisional, "baseline": sc.score.baseline,
            "sample_size": sc.score.sample_size, "format": _format(video.is_short),
            "status": sc.score.status,
        }
        if sc is not None
        else {"status": "insufficient_data", "score": None, "bracket": None,
              "provisional": False, "baseline": None, "sample_size": 0,
              "format": _format(video.is_short)}
    )

    return {
        "video": {
            "video_id": video.video_id, "title": video.title,
            "published_at": video.published_at.isoformat() if video.published_at else None,
            "duration_s": video.duration_s, "is_short": video.is_short,
            "thumbnail_url": video.thumbnail_url,
        },
        "snapshots": [
            {"captured_at": s.captured_at.isoformat(), "views": s.views,
             "likes": s.likes, "comments": s.comments}
            for s in crepo.list_video_snapshots(session, video_id)
        ],
        "vph": {
            "current": core.current_vph(snaps, window_hours=settings.vph_current_window_hours),
            "series": [
                {"from": p.from_at.isoformat(), "to": p.to_at.isoformat(),
                 "hours": p.hours, "vph": p.vph}
                for p in core.vph_series(snaps)
            ],
        },
        "outlier": outlier,
        "engagement": {"like_rate": eng.like_rate, "comment_rate": eng.comment_rate},
        "resurgence": {
            "is_resurgent": res.is_resurgent, "ratio": res.ratio,
            "current_vph": res.current_vph, "recent_avg_vph": res.recent_avg_vph,
        },
        "attributes": (
            {"source": ATTR_SOURCE, **attrs_row.attributes} if attrs_row is not None else None
        ),
        "packaging_changes": [
            {"field": c.field, "old_value": c.old_value, "new_value": c.new_value,
             "detected_at": c.detected_at.isoformat(),
             "vph_before": c.vph_before, "vph_after": c.vph_after}
            for c in changes
        ],
    }


# ------------------------- channel dashboard -------------------------


def channel_dashboard(session, channel_id, *, settings=None, now=None) -> dict | None:
    settings = settings or get_settings()
    now = _now(now)
    channel = crepo.get_channel(session, channel_id)
    if channel is None:
        return None

    scored, baselines = _channel_scores(session, channel_id, settings=settings, now=now)
    videos = crepo.list_channel_videos(session, channel_id)
    snap = crepo.latest_channel_snapshot(session, channel_id)

    # Outlier ranking (>= the first bracket), most-viral first.
    outliers = sorted(
        (sc for sc in scored.values() if sc.score.bracket is not None),
        key=lambda sc: sc.score.score, reverse=True,
    )

    # Engagement medians across the channel's videos that have a snapshot.
    like_rates, comment_rates = [], []
    for v in videos:
        latest = crepo.latest_video_snapshot(session, v.video_id)
        if latest is None:
            continue
        e = core.engagement_rates(latest.views, latest.likes, latest.comments)
        if e.like_rate is not None:
            like_rates.append(e.like_rate)
        if e.comment_rate is not None:
            comment_rates.append(e.comment_rate)

    cad = core.cadence(
        [core.VideoMeta(v.published_at, v.is_short, v.duration_s) for v in videos], now=now
    )

    resurgences = []
    for v in videos:
        res = _resurgence_for(session, v, settings=settings, now=now)
        if res.is_resurgent:
            resurgences.append({
                "video_id": v.video_id, "title": v.title, "ratio": res.ratio,
                "current_vph": res.current_vph, "recent_avg_vph": res.recent_avg_vph,
            })

    # Packaging log with its persisted before/after impact (same shape as the
    # project dashboard, scoped to this channel).
    video_title = {v.video_id: v.title for v in videos}
    packaging = []
    for ch in crepo.list_packaging_changes(session, channel_id=channel_id):
        delta = (
            ch.vph_after - ch.vph_before
            if ch.vph_after is not None and ch.vph_before is not None else None
        )
        packaging.append({
            "video_id": ch.video_id, "title": video_title.get(ch.video_id, ""),
            "field": ch.field, "detected_at": ch.detected_at.isoformat(),
            "vph_before": ch.vph_before, "vph_after": ch.vph_after, "delta": delta,
        })

    return {
        "channel": {
            "channel_id": channel.channel_id, "title": channel.title, "handle": channel.handle,
            "subscribers": snap.subscribers if snap else None,
            "total_views": snap.total_views if snap else None,
            "video_count": snap.video_count if snap else None,
        },
        "baselines": {
            fmt: ({"median": b.median, "sample": b.sample_size} if b else None)
            for fmt, b in baselines.items()
        },
        "outliers": [_outlier_payload(sc) for sc in outliers],
        "engagement": {
            "like_rate_median": core.median(like_rates),
            "comment_rate_median": core.median(comment_rates),
        },
        "cadence": {
            "uploads_per_week": cad.uploads_per_week,
            "by_weekday": {str(k): v for k, v in sorted(cad.by_weekday.items())},
            "by_hour": {str(k): v for k, v in sorted(cad.by_hour.items())},
            "duration_by_format": cad.duration_by_format,
        },
        "packaging": packaging,
        "resurgences": resurgences,
    }


# ------------------------- project dashboard -------------------------


def project_dashboard(session, project_id, *, settings=None, now=None) -> dict | None:
    settings = settings or get_settings()
    now = _now(now)
    project = crepo.get_project(session, project_id)
    if project is None:
        return None

    channels = crepo.list_project_channels(session, project_id)
    channel_title = {c.channel_id: c.title for c in channels}
    video_title = {
        v.video_id: v.title
        for c in channels
        for v in crepo.list_channel_videos(session, c.channel_id)
    }

    # Score each channel once (channel metrics are project-independent), then merge.
    all_scored: list[_Scored] = []
    for c in channels:
        scored, _ = _channel_scores(session, c.channel_id, settings=settings, now=now)
        all_scored.extend(scored.values())

    ranked = sorted(
        (sc for sc in all_scored if sc.score.bracket is not None),
        key=lambda sc: sc.score.score, reverse=True,
    )

    # Conditional distributions: population = project videos with attributes;
    # outliers = the definitive (non-provisional, >= threshold) subset.
    threshold = settings.pattern_outlier_bracket
    project_video_ids = [sc.video_id for sc in all_scored]
    attrs = crepo.list_video_attributes(session, project_video_ids, ATTR_SOURCE)
    population_attrs = [attrs[sc.video_id].attributes for sc in all_scored if sc.video_id in attrs]
    outlier_attrs = [
        attrs[sc.video_id].attributes
        for sc in all_scored
        if sc.video_id in attrs and _is_definitive_outlier(sc, threshold=threshold)
    ]
    patterns = conditional_patterns(
        population_attrs, outlier_attrs, min_occurrence=settings.pattern_min_occurrence
    )

    # Packaging changes across the project's channels, with their persisted impact.
    packaging = []
    for c in channels:
        for ch in crepo.list_packaging_changes(session, channel_id=c.channel_id):
            delta = (
                ch.vph_after - ch.vph_before
                if ch.vph_after is not None and ch.vph_before is not None else None
            )
            packaging.append({
                "video_id": ch.video_id, "channel_id": c.channel_id,
                "title": video_title.get(ch.video_id, ""),
                "channel_title": channel_title.get(c.channel_id, ""),
                "field": ch.field, "detected_at": ch.detected_at.isoformat(),
                "vph_before": ch.vph_before, "vph_after": ch.vph_after, "delta": delta,
            })

    # Resurgences across the project's channels.
    resurgences = []
    for c in channels:
        for v in crepo.list_channel_videos(session, c.channel_id):
            res = _resurgence_for(session, v, settings=settings, now=now)
            if res.is_resurgent:
                resurgences.append({
                    "video_id": v.video_id, "channel_id": c.channel_id,
                    "title": v.title, "ratio": res.ratio, "current_vph": res.current_vph,
                })

    return {
        "project": {"id": project.id, "name": project.name},
        "channels": [
            {"channel_id": c.channel_id, "title": c.title,
             "subscribers": (s.subscribers if (s := crepo.latest_channel_snapshot(session, c.channel_id)) else None)}
            for c in channels
        ],
        "outliers": [
            _outlier_payload(
                sc, with_channel=True,
                vph=core.current_vph(_snaps(session, sc.video_id),
                                     window_hours=settings.vph_current_window_hours),
            ) | {"channel_title": channel_title.get(sc.channel_id, "")}
            for sc in ranked
        ],
        "patterns": [
            {"attribute": p.attribute, "outlier_rate": p.outlier_rate,
             "population_rate": p.population_rate, "lift": p.lift,
             "outlier_occurrences": p.outlier_occurrences}
            for p in patterns
        ],
        "packaging": packaging,
        "resurgences": resurgences,
    }
