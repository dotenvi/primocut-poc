"""DAO for Phase 3 AI packaging: per-item packaging artifacts + the live's audience
match. Pure DB access; the pay-once cache (`content_hash`) and the state machine
live in `packaging.generate`, the same split as `metrics.enrichment`.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import CutPackagingRow, LiveAudienceMatchRow

KINDS = ("title", "thumb_brief", "frame_ranking")


# ------------------------- cut packaging (per item) -------------------------


def get_packaging(s: Session, cut_item_id: int, kind: str) -> CutPackagingRow | None:
    return s.execute(
        select(CutPackagingRow).where(
            CutPackagingRow.cut_item_id == cut_item_id,
            CutPackagingRow.kind == kind,
        )
    ).scalars().first()


def list_packaging_for_cut(s: Session, cut_id: int) -> list[CutPackagingRow]:
    return list(
        s.execute(
            select(CutPackagingRow).where(CutPackagingRow.cut_id == cut_id)
            .order_by(CutPackagingRow.cut_item_id.asc(), CutPackagingRow.kind.asc())
        ).scalars()
    )


def packaging_by_item(s: Session, cut_id: int) -> dict[int, dict[str, CutPackagingRow]]:
    """{cut_item_id: {kind: row}} for one cut — the bench/export fold this in."""
    out: dict[int, dict[str, CutPackagingRow]] = {}
    for row in list_packaging_for_cut(s, cut_id):
        out.setdefault(row.cut_item_id, {})[row.kind] = row
    return out


def upsert_packaging(
    s: Session, *, cut_item_id: int, cut_id: int, kind: str, payload: dict,
    content_hash: str = "", state: str = "done", error: str = "",
) -> CutPackagingRow:
    row = get_packaging(s, cut_item_id, kind)
    if row is None:
        row = CutPackagingRow(cut_item_id=cut_item_id, kind=kind)
        s.add(row)
    row.cut_id = cut_id
    row.payload = payload
    row.content_hash = content_hash
    row.state = state
    row.error = error
    s.flush()
    return row


def set_packaging_state(s: Session, cut_item_id: int, kind: str, state: str,
                        **fields) -> None:
    row = get_packaging(s, cut_item_id, kind)
    if row is None:
        return
    row.state = state
    for k, v in fields.items():
        setattr(row, k, v)
    s.flush()


def delete_packaging_for_item(s: Session, cut_item_id: int) -> None:
    s.execute(delete(CutPackagingRow).where(CutPackagingRow.cut_item_id == cut_item_id))
    s.flush()


# ------------------------- audience match (per live) -------------------------


def get_audience_match(s: Session, video_id: str) -> LiveAudienceMatchRow | None:
    return s.get(LiveAudienceMatchRow, video_id)


def upsert_audience_match(
    s: Session, *, video_id: str, payload: dict, content_hash: str,
    state: str = "done", error: str = "",
) -> LiveAudienceMatchRow:
    row = s.get(LiveAudienceMatchRow, video_id)
    if row is None:
        row = LiveAudienceMatchRow(video_id=video_id)
        s.add(row)
    row.payload = payload
    row.content_hash = content_hash
    row.state = state
    row.error = error
    s.flush()
    return row
