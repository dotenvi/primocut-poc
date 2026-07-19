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
from . import comments as mcomments
from . import core
from .attributes import SOURCE as ATTR_SOURCE
from .enrichment import KIND_THUMBNAIL, KIND_TITLE, SOURCE_THUMBNAIL, SOURCE_TITLE
from .patterns import conditional_patterns

# The enrichment kinds whose result feeds `_patterns_for` — a comparison of two
# groups (outliers vs. control). Both sides must be enriched or a proportion has
# nothing to compare, so their batch target is every MATURE video, not just the
# outliers. `comments` synthesises a single group and stays outliers-only.
_PATTERN_KINDS = (KIND_TITLE, KIND_THUMBNAIL)

# Every attribute source a dashboard reads. `syntactic` is always present (it is
# deterministic and free); the two LLM ones exist only for videos the user chose
# to enrich, which is exactly why each is measured against its own population.
ATTR_SOURCES = (ATTR_SOURCE, SOURCE_TITLE, SOURCE_THUMBNAIL)


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


# Shorts and long-form are different products: different monetisation, different
# audience, different format. Every population a dashboard reports is therefore
# split by format — a figure blended across both describes neither. `FORMATS`
# fixes the key order so a payload is stable and a caller can iterate it.
FORMATS: tuple[tuple[str, bool], ...] = (("long", False), ("short", True))


def _by_format(items, is_short_of) -> dict[str, list]:
    """Split `items` into the per-format lists the dashboards report."""
    return {
        fmt: [it for it in items if is_short_of(it) == is_short]
        for fmt, is_short in FORMATS
    }


# ------------------------- channel-level outlier scoring -------------------------


def _band_config(settings: Settings) -> tuple[str, dict[str, float]]:
    """The active band basis and its three cutoffs (global — never per-project)."""
    basis = settings.band_basis
    spec = (
        settings.band_cutoffs_percentile if basis == "percentile"
        else settings.band_cutoffs_fixed
    )
    return basis, core.parse_cutoffs(spec)


def _window_peers(videos, views_by_id, *, is_short: bool, settings: Settings, now: datetime):
    """The same-format rolling window that feeds the band ruler: the smaller of
    "the last N videos" and "the last D days", scored videos only. This is the
    single definition of the peer set — the ruler and the spread histogram both
    read it, so a channel's classification and its distribution never diverge."""
    window_cut = now - timedelta(days=settings.outlier_window_days)
    peers = [
        v for v in videos
        if v.is_short == is_short
        and v.published_at is not None
        and v.published_at >= window_cut
        and v.video_id in views_by_id
    ]
    peers.sort(key=lambda v: v.published_at, reverse=True)
    return peers[: settings.outlier_window_videos]


def band_config(settings=None) -> dict:
    """The active outlier band configuration (global, read-only) for the Settings
    UI: the basis and the three ordinal cutoffs. There is no per-project override —
    bands are a pure per-channel metric (see `band_ruler`)."""
    settings = settings or get_settings()
    basis, cutoffs = _band_config(settings)
    return {"basis": basis, "cutoffs": cutoffs}


def _channel_scores(
    session, channel_id: str, *, settings: Settings, now: datetime
) -> tuple[dict[str, _Scored], dict[str, core.BandRuler | None]]:
    """Classify every video of a channel against its per-format band ruler.

    The ruler for each format is built from the median AND the band cutoffs of the
    same-format videos in the rolling window = the smaller of "the last N videos"
    and "the last D days". Returns (video_id -> _Scored, format -> BandRuler|None).
    """
    videos = crepo.list_channel_videos(session, channel_id)
    views_by_id = crepo.latest_views(session, [v.video_id for v in videos])
    basis, cutoffs = _band_config(settings)
    provisional_cut = now - timedelta(days=settings.outlier_provisional_days)

    rulers: dict[str, core.BandRuler | None] = {}
    for fmt, is_short in FORMATS:
        peers = _window_peers(videos, views_by_id, is_short=is_short, settings=settings, now=now)
        rulers[fmt] = core.band_ruler(
            [views_by_id[v.video_id] for v in peers],
            basis=basis, cutoffs=cutoffs, min_sample=settings.outlier_min_sample,
        )

    scored: dict[str, _Scored] = {}
    for v in videos:
        if v.video_id not in views_by_id:
            continue  # no snapshot yet — cannot be scored
        provisional = v.published_at is None or v.published_at > provisional_cut
        score = core.outlier_score(
            views_by_id[v.video_id], rulers[_format(v.is_short)],
            provisional=provisional,
        )
        scored[v.video_id] = _Scored(
            v.video_id, channel_id, v.title, v.is_short, views_by_id[v.video_id], score
        )
    return scored, rulers


def _is_definitive_outlier(sc: _Scored) -> bool:
    """A mature (non-provisional, sufficient-baseline) video that reached a band."""
    return sc.score.status == "ok" and sc.score.band is not None


def _is_mature(sc: _Scored) -> bool:
    """A video with a real, settled score — the exact population `_patterns_for`
    compares over: BOTH the banded outliers and the band-less control. Provisional
    (< 7 days) and insufficient-sample videos are `status != "ok"` and stay out."""
    return sc.score.status == "ok"


def _outlier_payload(sc: _Scored, *, with_channel: bool = False, vph: float | None = None) -> dict:
    out = {
        "video_id": sc.video_id,
        "title": sc.title,
        "is_short": sc.is_short,
        "views": sc.views,
        "median_multiple": sc.score.median_multiple,
        "band": sc.score.band,
        "band_basis": sc.score.band_basis,
        "percentile": sc.score.percentile,
        "band_cutoffs": sc.score.band_cutoffs,
        "provisional": sc.score.provisional,
    }
    if with_channel:
        out["channel_id"] = sc.channel_id
        out["current_vph"] = vph
    return out


# ------------------------- channel spread distribution -------------------------


def _distribution_payload(
    channel_id: str, fmt: str, peer_views: list[int], *, settings: Settings
) -> dict:
    """The "channel spread" strip for one channel+format: the histogram of the
    window's view counts plus the band thresholds, so the same render marks any
    video by its own view count. No per-video snapshot scan — the caller passes
    the already-fetched window views."""
    basis, cutoffs = _band_config(settings)
    ruler = core.band_ruler(
        peer_views, basis=basis, cutoffs=cutoffs, min_sample=settings.outlier_min_sample
    )
    hist = core.spread_histogram(peer_views)
    return {
        "channel_id": channel_id,
        "format": fmt,
        "count": len(peer_views),
        "basis": basis,
        "median": ruler.median if ruler else None,
        "thresholds": ruler.thresholds if ruler else None,
        "min": hist.min if hist else None,
        "max": hist.max if hist else None,
        "buckets": hist.counts if hist else [],
        "bucket_edges": hist.edges if hist else [],
    }


def channel_distribution(
    session, channel_id: str, *, fmt: str, settings=None, now=None
) -> dict | None:
    """One channel's spread for `fmt` ('long'|'short'). `None` if the channel is
    unknown. Two queries (videos + latest views) — never scans snapshots."""
    settings = settings or get_settings()
    now = _now(now)
    if crepo.get_channel(session, channel_id) is None:
        return None
    videos = crepo.list_channel_videos(session, channel_id)
    views_by_id = crepo.latest_views(session, [v.video_id for v in videos])
    peers = _window_peers(
        videos, views_by_id, is_short=(fmt == "short"), settings=settings, now=now
    )
    return _distribution_payload(
        channel_id, fmt, [views_by_id[v.video_id] for v in peers], settings=settings
    )


def channel_distributions(
    session, channel_ids, *, fmt: str, settings=None, now=None
) -> dict[str, dict]:
    """Batch spread for the Radar: `channel_id -> distribution` for every known
    id. Query count is bounded by the number of channels — one video list per
    channel plus a SINGLE batched `latest_views` over their union — and never
    grows with videos-per-channel, so a Radar page is free of N+1 and of any
    snapshot scan. Unknown ids are skipped."""
    settings = settings or get_settings()
    now = _now(now)
    is_short = fmt == "short"
    videos_by_channel: dict[str, list] = {}
    all_ids: list[str] = []
    for cid in channel_ids:
        if crepo.get_channel(session, cid) is None:
            continue
        vids = crepo.list_channel_videos(session, cid)
        videos_by_channel[cid] = vids
        all_ids.extend(v.video_id for v in vids)
    views_by_id = crepo.latest_views(session, all_ids)
    out: dict[str, dict] = {}
    for cid, vids in videos_by_channel.items():
        peers = _window_peers(vids, views_by_id, is_short=is_short, settings=settings, now=now)
        out[cid] = _distribution_payload(
            cid, fmt, [views_by_id[v.video_id] for v in peers], settings=settings
        )
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


# ------------------------- attributes -------------------------


def _attributes_by_source(session, video_id: str) -> dict:
    """One entry per source, `None` where the video was never analysed for it.

    The enrichment sources also carry their `state`/`error` so the UI can render a
    run in flight, or a failure, instead of an empty panel.
    """
    out: dict[str, dict | None] = {}
    for source in ATTR_SOURCES:
        row = crepo.get_video_attributes(session, video_id, source)
        out[source] = (
            {"values": row.attributes, "state": row.state, "error": row.error,
             "updated_at": row.updated_at.isoformat()}
            if row is not None else None
        )
    return out


def _patterns_for(session, all_scored: list[_Scored], *, settings: Settings) -> list[dict]:
    """Conditional distributions, computed once per attribute source.

    The outlier group is the banded, mature videos; the control is the banded==
    null, mature videos. Provisional (< 7 days) and insufficient-sample videos are
    censored from both. Each source's groups are restricted to the videos that
    actually have a row for it — an un-enriched video is *unknown*, not "attribute
    absent".
    """
    video_ids = [sc.video_id for sc in all_scored]
    mature = [sc for sc in all_scored if sc.score.status == "ok"]
    out: list[dict] = []
    for source in ATTR_SOURCES:
        attrs = crepo.list_video_attributes(session, video_ids, source)
        outliers = [
            attrs[sc.video_id].attributes
            for sc in mature
            if sc.video_id in attrs and sc.score.band is not None
        ]
        control = [
            attrs[sc.video_id].attributes
            for sc in mature
            if sc.video_id in attrs and sc.score.band is None
        ]
        out.extend(
            {"attribute": p.attribute, "freq_outlier": p.freq_outlier,
             "ci_outlier": list(p.ci_outlier), "freq_control": p.freq_control,
             "ci_control": list(p.ci_control), "lift": p.lift,
             "n_outlier": p.n_outlier, "n_control": p.n_control,
             "verdict": p.verdict, "source": p.source}
            for p in conditional_patterns(
                outliers, control, z=settings.pattern_confidence, source=source,
            )
        )
    return out


def _comment_synthesis(session, video_id: str) -> dict | None:
    """The synthesis row, or None where the user never asked for one.

    Same envelope as an enrichment attributes entry (`values`/`state`/`error`), so
    the UI polls an in-flight synthesis with the very same predicate.
    """
    row = crepo.get_video_enrichment(session, video_id, mcomments.KIND)
    if row is None:
        return None
    return {
        "values": row.payload,
        "state": row.state,
        "error": row.error,
        "updated_at": row.updated_at.isoformat(),
    }


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
    changes = crepo.list_packaging_changes(session, video_id=video_id)

    outlier = (
        {
            "median_multiple": sc.score.median_multiple, "band": sc.score.band,
            "band_basis": sc.score.band_basis, "percentile": sc.score.percentile,
            "band_cutoffs": sc.score.band_cutoffs,
            "provisional": sc.score.provisional, "baseline": sc.score.baseline,
            "sample_size": sc.score.sample_size, "format": _format(video.is_short),
            "status": sc.score.status,
        }
        if sc is not None
        else {"status": "insufficient_sample", "median_multiple": None, "band": None,
              "band_basis": "", "percentile": None, "band_cutoffs": {},
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
        "attributes": _attributes_by_source(session, video_id),
        # Collection state and synthesis are reported separately: the comments are
        # collected automatically, the synthesis over them is not (guideline 9),
        # so the UI must be able to say "collected, not synthesised".
        "comments": {
            "collected": crepo.count_video_comments(session, video_id),
            "state": video.comments_state,
            "error": video.comments_error,
        },
        "comment_synthesis": _comment_synthesis(session, video_id),
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

    scored, rulers = _channel_scores(session, channel_id, settings=settings, now=now)
    videos = crepo.list_channel_videos(session, channel_id)
    snap = crepo.latest_channel_snapshot(session, channel_id)

    # Outlier ranking (reached a band), most-viral first (by median multiple), per
    # format.
    outliers = _by_format(
        sorted(
            (sc for sc in scored.values() if sc.score.band is not None),
            key=lambda sc: sc.score.median_multiple, reverse=True,
        ),
        lambda sc: sc.is_short,
    )

    # Engagement medians over the channel's videos that have a snapshot, per
    # format: a Short's like rate is structurally unlike a long-form video's.
    engagement = {}
    for fmt, videos_of in _by_format(videos, lambda v: v.is_short).items():
        like_rates, comment_rates = [], []
        for v in videos_of:
            latest = crepo.latest_video_snapshot(session, v.video_id)
            if latest is None:
                continue
            e = core.engagement_rates(latest.views, latest.likes, latest.comments)
            if e.like_rate is not None:
                like_rates.append(e.like_rate)
            if e.comment_rate is not None:
                comment_rates.append(e.comment_rate)
        engagement[fmt] = {
            "like_rate_median": core.median(like_rates),
            "comment_rate_median": core.median(comment_rates),
        }

    cad = core.cadence(
        [core.VideoMeta(v.published_at, v.is_short, v.duration_s) for v in videos], now=now
    )

    resurgent = []
    for v in videos:
        res = _resurgence_for(session, v, settings=settings, now=now)
        if res.is_resurgent:
            resurgent.append((v.is_short, {
                "video_id": v.video_id, "title": v.title, "ratio": res.ratio,
                "current_vph": res.current_vph, "recent_avg_vph": res.recent_avg_vph,
            }))
    resurgences = {
        fmt: [payload for _, payload in rows]
        for fmt, rows in _by_format(resurgent, lambda r: r[0]).items()
    }

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
            fmt: (
                {"median": r.median, "sample": r.sample_size, "basis": r.basis,
                 "cutoffs": r.cutoffs, "thresholds": r.thresholds}
                if r else None
            )
            for fmt, r in rulers.items()
        },
        "outliers": {
            fmt: [_outlier_payload(sc) for sc in scs] for fmt, scs in outliers.items()
        },
        "engagement": engagement,
        "cadence": {
            "uploads_per_week": cad.uploads_per_week,
            "by_weekday": {str(k): v for k, v in sorted(cad.by_weekday.items())},
            "by_hour": {str(k): v for k, v in sorted(cad.by_hour.items())},
            "duration_by_format": cad.duration_by_format,
        },
        "packaging": packaging,
        "resurgences": resurgences,
    }


# ------------------------- project-scoped selection -------------------------


def definitive_outlier_ids(session, project_id, *, settings=None, now=None) -> list[str] | None:
    """The project's definitive outliers — the target for `comments` enrichment.

    A provisional video (< 7 days) may still fall back to normal as its score
    matures, so only settled outliers are worth synthesising. Returns None when the
    project does not exist.

    NOTE: this is NOT the whole set `_patterns_for` reads. That comparison also
    needs the band-less control, so `title`/`thumbnail` target every mature video
    via `enrichment_target_ids` — see `_PATTERN_KINDS`.
    """
    return _project_video_ids(
        session, project_id, _is_definitive_outlier, settings=settings, now=now
    )


def mature_video_ids(session, project_id, *, settings=None, now=None) -> list[str] | None:
    """Every mature video of the project — the population `_patterns_for` compares,
    i.e. the banded outliers AND the band-less control together. Returns None when
    the project does not exist."""
    return _project_video_ids(
        session, project_id, _is_mature, settings=settings, now=now
    )


def _project_video_ids(session, project_id, keep, *, settings, now) -> list[str] | None:
    settings = settings or get_settings()
    now = _now(now)
    if crepo.get_project(session, project_id) is None:
        return None
    out: list[str] = []
    for c in crepo.list_project_channels(session, project_id):
        scored, _ = _channel_scores(session, c.channel_id, settings=settings, now=now)
        out.extend(sc.video_id for sc in scored.values() if keep(sc))
    return out


def enrichment_target_ids(
    session, project_id, kind: str, *, settings=None, now=None
) -> list[str] | None:
    """The videos an opt-in enrichment of `kind` should cover, or None if the
    project does not exist.

    `title`/`thumbnail` feed the two-group pattern comparison, so BOTH sides must
    carry the attribute — the target is every mature video. `comments` summarise a
    single group, so they target only the definitive outliers.
    """
    select = mature_video_ids if kind in _PATTERN_KINDS else definitive_outlier_ids
    return select(session, project_id, settings=settings, now=now)


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

    ranked = _by_format(
        sorted(
            (sc for sc in all_scored if sc.score.band is not None),
            key=lambda sc: sc.score.median_multiple, reverse=True,
        ),
        lambda sc: sc.is_short,
    )

    # Conditional distributions, per source: outliers = the banded mature videos
    # with a row for that source; control = the banded==null mature ones. Computed
    # per format, so an attribute's lift measures it against same-format peers and
    # never against the gap between Shorts and long-form. Wilson intervals gate
    # what shows, so a thin format simply separates nothing and reports nothing.
    patterns = {
        fmt: _patterns_for(session, scs, settings=settings)
        for fmt, scs in _by_format(all_scored, lambda sc: sc.is_short).items()
    }

    # What this niche's audience asked for, merged across the project's syntheses.
    # Scoped to the project (a request in another niche never leaks in) and
    # aggregated in code — no second LLM pass.
    audience_requests = mcomments.aggregate_requests(
        [
            (row.video_id, row.payload)
            for row in crepo.list_project_enrichments(session, project_id, mcomments.KIND)
        ]
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
    resurgent = []
    for c in channels:
        for v in crepo.list_channel_videos(session, c.channel_id):
            res = _resurgence_for(session, v, settings=settings, now=now)
            if res.is_resurgent:
                resurgent.append((v.is_short, {
                    "video_id": v.video_id, "channel_id": c.channel_id,
                    "title": v.title, "ratio": res.ratio, "current_vph": res.current_vph,
                }))
    resurgences = {
        fmt: [payload for _, payload in rows]
        for fmt, rows in _by_format(resurgent, lambda r: r[0]).items()
    }

    return {
        "project": {"id": project.id, "name": project.name},
        "channels": [
            {"channel_id": c.channel_id, "title": c.title,
             "subscribers": (s.subscribers if (s := crepo.latest_channel_snapshot(session, c.channel_id)) else None)}
            for c in channels
        ],
        "outliers": {
            fmt: [
                _outlier_payload(
                    sc, with_channel=True,
                    vph=core.current_vph(_snaps(session, sc.video_id),
                                         window_hours=settings.vph_current_window_hours),
                ) | {"channel_title": channel_title.get(sc.channel_id, "")}
                for sc in scs
            ]
            for fmt, scs in ranked.items()
        },
        "patterns": patterns,
        "audience_requests": audience_requests,
        "packaging": packaging,
        "resurgences": resurgences,
    }
