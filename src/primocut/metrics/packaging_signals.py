"""Deterministic INPUT for the Phase 3 AI packaging (no LLM, guideline 9/10).

`build_packaging_context` reads a project's Phase 2 patterns and re-presents them
under the three lenses the packaging prompt cites, plus real outlier examples:

- differential  — the conditional-distribution patterns (`metrics.patterns`,
  Wilson-gated) that SEPARATE outliers from the censored control. Reused verbatim
  from the dashboard machinery so packaging can never disagree with the niche view.
- conventions   — booleans near-universal across the WHOLE mature niche (frequency
  >= `packaging_convention_floor`). They are what the niche REQUIRES (table-stakes),
  not what distinguishes a winner: high in both groups, so their Wilson intervals
  overlap and the differential guard drops them. Never double-reported as a
  differential.
- carries_value — recurring NON-boolean values (the "name that carries", from
  `title_llm.person_names`) that appear disproportionately in outliers. Guarded by
  a min-videos AND min-lift floor — the never-invent-a-pattern rule for values,
  mirroring the Wilson guard for booleans.

Everything is a pure read over the same scoring `metrics.service` computes; scores
are never stored (guideline 10). A niche where nothing separates and nothing
carries is reported `flat=True` so the generator stays honest (packaging is not the
lever) instead of inventing differentials.
"""

from __future__ import annotations

from collections import defaultdict

from ..config import Settings, get_settings
from ..db import competitors as crepo
from . import service
from .enrichment import SOURCE_TITLE

# The value fields a carries-value pattern is mined from, per attribute source.
# Today only the title's person names (the canonical "name that carries"); the
# structure leaves room for more list-valued fields without touching the callers.
_CARRIES_FIELDS = {SOURCE_TITLE: ("person_names",)}


def build_packaging_context(
    session, project_id, *, fmt: str | None = None, settings: Settings | None = None,
    now=None,
) -> dict | None:
    """The packaging input for the LIVE's project, or None if it does not exist.

    `fmt` selects which niche format's patterns drive packaging (clips are usually
    posted long-form); it defaults to `settings.packaging_format`.
    """
    settings = settings or get_settings()
    fmt = fmt or settings.packaging_format
    now = service._now(now)

    project = crepo.get_project(session, project_id)
    if project is None:
        return None

    channels = crepo.list_project_channels(session, project_id)
    all_scored: list = []
    for c in channels:
        scored, _ = service._channel_scores(session, c.channel_id, settings=settings, now=now)
        all_scored.extend(scored.values())

    scored_fmt = service._by_format(all_scored, lambda sc: sc.is_short).get(fmt, [])
    mature = [sc for sc in scored_fmt if sc.score.status == "ok"]
    outliers = [sc for sc in mature if sc.score.band is not None]

    differential = service._patterns_for(session, scored_fmt, settings=settings)
    diff_attrs = {d["attribute"] for d in differential}
    conventions = _conventions(session, mature, diff_attrs, settings=settings)
    carries_value = _carries_value(session, mature, outliers, settings=settings)
    examples = _examples(session, outliers, settings=settings)

    # Flat: nothing separates outliers and nothing carries. Conventions may still
    # exist (everyone does X), but a requirement is not a lever, so it does not lift
    # the niche out of flatness on its own.
    flat = not differential and not any(carries_value.values())

    return {
        "niche": {
            "project_name": project.name,
            "channels": len(channels),
            "outliers": len(outliers),
            "format": fmt,
        },
        "differential": differential,
        "conventions": conventions,
        "carries_value": carries_value,
        "examples": examples,
        "flat": flat,
    }


def _grouped_attributes(session, videos: list, source: str) -> dict[str, dict]:
    """{video_id: attributes} for the videos that have a row for `source`."""
    ids = [sc.video_id for sc in videos]
    rows = crepo.list_video_attributes(session, ids, source)
    return {vid: row.attributes for vid, row in rows.items()}


def _conventions(session, mature: list, diff_attrs: set, *, settings: Settings) -> list[dict]:
    """Booleans present in >= `convention_floor` of the enriched mature niche.

    Computed over BOTH groups together (a requirement is a niche fact, not an
    outlier one). Anything already reported as a differential is excluded — a
    separating attribute is a lever, not a convention.
    """
    floor = settings.packaging_convention_floor
    out: list[dict] = []
    for source in service.ATTR_SOURCES:
        attrs = _grouped_attributes(session, mature, source)
        if not attrs:
            continue
        n = len(attrs)
        keys = {k for a in attrs.values() for k, v in a.items() if isinstance(v, bool)}
        for key in sorted(keys):
            if key in diff_attrs:
                continue
            hits = sum(1 for a in attrs.values() if a.get(key) is True)
            freq = hits / n
            if freq >= floor:
                out.append({"attribute": key, "source": source, "freq": freq, "n": n})
    out.sort(key=lambda c: c["freq"], reverse=True)
    return out


def _carries_value(session, mature: list, outliers: list, *, settings: Settings) -> dict:
    """Recurring list-valued attributes (the name that carries) mined from outliers.

    For each value, compare how many OUTLIER videos carry it against how many
    CONTROL videos do (both restricted to videos enriched for the source). A value
    is reported only when it clears the min-videos AND min-lift floor — a value seen
    once is not a pattern (the never-invent guard for non-boolean values).
    """
    out_ids = {sc.video_id for sc in outliers}
    result: dict[str, list[dict]] = {}
    for source, fields in _CARRIES_FIELDS.items():
        attrs = _grouped_attributes(session, mature, source)
        out_enriched = [vid for vid in attrs if vid in out_ids]
        ctl_enriched = [vid for vid in attrs if vid not in out_ids]
        n_out, n_ctl = len(out_enriched), len(ctl_enriched)
        for field in fields:
            result[field] = _mine_field(
                attrs, out_enriched, ctl_enriched, field, n_out, n_ctl, settings=settings
            )
    return result


def _mine_field(attrs, out_ids, ctl_ids, field, n_out, n_ctl, *, settings) -> list[dict]:
    """Per-value outlier-vs-control frequency of one list-valued attribute."""
    # value_key -> {"spellings": {raw: count}, "out": set(vid), "ctl": set(vid)}
    acc: dict[str, dict] = defaultdict(
        lambda: {"spellings": defaultdict(int), "out": set(), "ctl": set()}
    )
    for vid in out_ids + ctl_ids:
        for raw in _values(attrs[vid].get(field)):
            key = raw.strip().lower()
            if not key:
                continue
            entry = acc[key]
            entry["spellings"][raw] += 1
            (entry["out"] if vid in out_ids else entry["ctl"]).add(vid)

    rows: list[dict] = []
    min_videos = settings.packaging_carries_value_min_videos
    min_lift = settings.packaging_carries_value_min_lift
    for _key, entry in acc.items():
        v_out, v_ctl = len(entry["out"]), len(entry["ctl"])
        freq_out = v_out / n_out if n_out else 0.0
        freq_ctl = v_ctl / n_ctl if n_ctl else 0.0
        lift = (freq_out / freq_ctl) if freq_ctl > 0 else None
        if v_out < min_videos:
            continue
        if lift is not None and lift < min_lift:
            continue
        spellings = entry["spellings"]
        rows.append({
            "value": max(spellings, key=lambda s: (spellings[s], -len(s))),
            "videos_outlier": v_out, "videos_control": v_ctl, "lift": lift,
        })
    rows.sort(key=lambda r: (r["lift"] is not None, r["lift"] or 0.0, r["videos_outlier"]),
              reverse=True)
    return rows


def _values(raw) -> list[str]:
    """Normalize a list-valued attribute to a list of non-empty strings."""
    if not isinstance(raw, list):
        return []
    return [v for v in raw if isinstance(v, str) and v.strip()]


def _examples(session, outliers: list, *, settings: Settings) -> list[dict]:
    """The top outliers by median multiple, with their enriched attributes.

    Real titles and real measured attributes the prompt can reference by video id —
    never fabricated. Attributes are whatever the user chose to enrich (may be
    empty); the example still carries the real title and band.
    """
    top = sorted(outliers, key=lambda sc: sc.score.median_multiple, reverse=True)
    top = top[: settings.packaging_examples]
    ids = [sc.video_id for sc in top]
    title_attrs = crepo.list_video_attributes(session, ids, SOURCE_TITLE)
    from .enrichment import SOURCE_THUMBNAIL

    thumb_attrs = crepo.list_video_attributes(session, ids, SOURCE_THUMBNAIL)
    out: list[dict] = []
    for sc in top:
        ta = title_attrs.get(sc.video_id)
        th = thumb_attrs.get(sc.video_id)
        out.append({
            "video_id": sc.video_id,
            "title": sc.title,
            "band": sc.score.band,
            "median_multiple": sc.score.median_multiple,
            "title_attrs": ta.attributes if ta is not None else {},
            "thumb_attrs": th.attributes if th is not None else {},
        })
    return out
