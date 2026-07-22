"""DAO for Phase 3 rendered clips (one MP4 deliverable per cut item).

Pure DB access. The pay-once cache key (`params_hash`) is over the render
parameters — range + padding + encode profile — so the same item re-rendered with
the same parameters is a cache hit; an explicit `regenerate` bypasses it. A clip
is 1:1 with its `cut_item` (unique `cut_item_id`); removing the item cascades the
row away, and the route unlinks the file.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ClipRow


def params_hash(
    start_s: float,
    end_s: float,
    padding_s: float,
    profile: str,
    tolerance_s: float = 0.0,
) -> str:
    """Cache key over the render parameters.

    Covers range + padding + encode profile + the WhisperX boundary tolerance
    (Phase 3 surgical refinement): changing the tolerance re-renders, since it can
    move the exact cut points; the same tolerance is a cache hit. The tolerance is
    a *requested* parameter — whether alignment was actually available at runtime
    is environmental (like the NVENC→libx264 fallback) and stays out of the key.
    """
    raw = f"{start_s:.3f}|{end_s:.3f}|{padding_s:.3f}|{profile}|{tolerance_s:.3f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_clip(s: Session, cut_id: int, clip_id: int) -> ClipRow | None:
    """A clip scoped to its cut: None if it does not exist or belongs elsewhere."""
    row = s.get(ClipRow, clip_id)
    if row is None or row.cut_id != cut_id:
        return None
    return row


def clip_for_item(s: Session, cut_item_id: int) -> ClipRow | None:
    return s.execute(
        select(ClipRow).where(ClipRow.cut_item_id == cut_item_id)
    ).scalars().first()


def list_clips_for_cut(s: Session, cut_id: int) -> list[ClipRow]:
    return list(
        s.execute(
            select(ClipRow).where(ClipRow.cut_id == cut_id)
            .order_by(ClipRow.position.asc(), ClipRow.id.asc())
        ).scalars()
    )


def clips_by_item(s: Session, cut_id: int) -> dict[int, ClipRow]:
    """{cut_item_id: clip} for one cut — the bench folds this into its items."""
    return {c.cut_item_id: c for c in list_clips_for_cut(s, cut_id)}


def reset_to_queued(
    s: Session,
    *,
    cut_item_id: int,
    video_id: str,
    cut_id: int,
    position: int,
    filename: str,
    rel_path: str,
    phash: str,
    padding_s: float,
    profile: str,
    start_s: float,
    end_s: float,
) -> ClipRow:
    """Create the item's clip row, or reset the existing one to `queued`.

    Called at enqueue time so the UI immediately shows a `queued` state. The
    parameter snapshot (range/padding/profile + `params_hash`) is stored now; the
    job fills `state`/`encoder_used`/`size_bytes` as it progresses.
    """
    row = clip_for_item(s, cut_item_id)
    if row is None:
        row = ClipRow(cut_item_id=cut_item_id)
        s.add(row)
    row.video_id = video_id
    row.cut_id = cut_id
    row.position = position
    row.filename = filename
    row.rel_path = rel_path
    row.params_hash = phash
    row.padding_s = padding_s
    row.encode_profile = profile
    row.start_s = start_s
    row.end_s = end_s
    row.state = "queued"
    row.error = ""
    row.encoder_used = ""
    row.size_bytes = 0
    row.duration_s = end_s - start_s
    s.flush()
    return row


def set_state(s: Session, clip_id: int, state: str, **fields) -> None:
    row = s.get(ClipRow, clip_id)
    if row is None:
        return
    row.state = state
    for k, v in fields.items():
        setattr(row, k, v)
    s.flush()


def delete_clip(s: Session, clip: ClipRow) -> None:
    s.delete(clip)
    s.flush()
