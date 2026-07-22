"""DAO for Phase 3 assisted cutting: cuts, cut items and per-topic triage.

Two deliberately separate concepts (see the 2026-07-19 decision log):

- **Triage** (`topic_triage`) is the user's opinion about a topic, scoped to the
  LIVE and shared across its cuts. Absence of a row means `open`; only
  `maybe`/`discarded` are stored. `in` is NEVER written here.
- **Membership** (`cut_items`) is belonging to one cut. The derived state `in`
  comes exclusively from the ACTIVE cut's items — the single source of truth.

Everything here is pure DB access; range/topic validation raises `ValueError`,
which the API layer turns into a 400. The session's transaction (opened by
`Database.session`) makes multi-row operations like reorder atomic.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import CutItemRow, CutRow, TopicRow, TopicTriageRow

_TRIAGE_STATES = {"maybe", "discarded"}


# ------------------------- Cuts -------------------------


def list_cuts(s: Session, video_id: str) -> list[CutRow]:
    """The live's cuts, oldest first (so the first is the default `Cut 1`)."""
    return list(
        s.execute(
            select(CutRow)
            .where(CutRow.video_id == video_id)
            .order_by(CutRow.created_at.asc(), CutRow.id.asc())
        ).scalars()
    )


def create_cut(s: Session, video_id: str, name: str = "Cut 1") -> CutRow:
    row = CutRow(video_id=video_id, name=name.strip() or "Cut 1")
    s.add(row)
    s.flush()
    return row


def ensure_default_cut(s: Session, video_id: str) -> CutRow:
    """Return the live's default cut, creating `Cut 1` lazily on first access."""
    existing = list_cuts(s, video_id)
    return existing[0] if existing else create_cut(s, video_id, "Cut 1")


def get_cut(s: Session, video_id: str, cut_id: int) -> CutRow | None:
    """A cut scoped to its live: None if it does not exist or belongs elsewhere."""
    row = s.get(CutRow, cut_id)
    if row is None or row.video_id != video_id:
        return None
    return row


def update_cut(s: Session, cut: CutRow, *, name: str | None = None,
               status: str | None = None) -> CutRow:
    if name is not None:
        cut.name = name.strip() or cut.name
    if status is not None:
        cut.status = status
    s.flush()
    return cut


def delete_cut(s: Session, cut: CutRow) -> None:
    """Delete a cut and its items (FK cascade). If it was the live's last cut, the
    next `ensure_default_cut` recreates an empty `Cut 1`."""
    s.execute(delete(CutRow).where(CutRow.id == cut.id))


# ------------------------- Items -------------------------


def list_items(s: Session, cut_id: int) -> list[CutItemRow]:
    return list(
        s.execute(
            select(CutItemRow)
            .where(CutItemRow.cut_id == cut_id)
            .order_by(CutItemRow.position.asc(), CutItemRow.id.asc())
        ).scalars()
    )


def cut_topic_ids(s: Session, cut_id: int) -> set[int]:
    """The topic ids that are `in` this cut (manual ranges carry no topic)."""
    rows = s.execute(
        select(CutItemRow.topic_id).where(CutItemRow.cut_id == cut_id)
    ).scalars()
    return {tid for tid in rows if tid is not None}


def _validate_range(start_s: float, end_s: float, duration: float) -> None:
    if start_s < 0 or end_s <= start_s:
        raise ValueError("range must satisfy 0 <= start < end")
    if duration and end_s > duration:
        raise ValueError("range exceeds the live duration")


def add_item(s: Session, cut: CutRow, *, duration: float,
             topic_id: int | None = None,
             start_s: float | None = None, end_s: float | None = None) -> CutItemRow:
    """Append an item at the end of the cut.

    A topic item (`topic_id` given) resolves and snapshots its start/end from the
    topic; adding a topic already in the cut is idempotent (returns the existing
    item), keeping the click cycle's `open -> in` safe to repeat. A manual range
    (`start_s`/`end_s` given, `topic_id` null) is validated against the live.
    """
    if topic_id is not None:
        topic = s.get(TopicRow, topic_id)
        if topic is None or topic.video_id != cut.video_id:
            raise ValueError("topic not found in this live")
        existing = s.execute(
            select(CutItemRow).where(
                CutItemRow.cut_id == cut.id, CutItemRow.topic_id == topic_id
            )
        ).scalars().first()
        if existing is not None:
            return existing
        start_s, end_s = topic.start, topic.end
    elif start_s is None or end_s is None:
        raise ValueError("a manual item requires start_s and end_s")

    _validate_range(float(start_s), float(end_s), duration)
    items = list_items(s, cut.id)
    row = CutItemRow(
        cut_id=cut.id, position=len(items), topic_id=topic_id,
        start_s=float(start_s), end_s=float(end_s),
    )
    s.add(row)
    s.flush()
    return row


def remove_item(s: Session, cut: CutRow, item_id: int) -> bool:
    """Remove an item and renumber the survivors to a contiguous 0..n-1."""
    row = s.get(CutItemRow, item_id)
    if row is None or row.cut_id != cut.id:
        return False
    s.delete(row)
    s.flush()
    for i, it in enumerate(list_items(s, cut.id)):
        it.position = i
    s.flush()
    return True


def reorder_items(s: Session, cut: CutRow, item_ids: list[int]) -> list[CutItemRow]:
    """Rewrite item positions from an ordered id list, transactionally.

    `item_ids` must be an exact permutation of the cut's current items; otherwise
    nothing is written and a `ValueError` is raised. All positions are reassigned
    in the same session transaction, so the reorder is atomic.
    """
    items = list_items(s, cut.id)
    if len(item_ids) != len(items) or {i.id for i in items} != set(item_ids):
        raise ValueError("item_ids must be a permutation of the cut's items")
    order = {iid: pos for pos, iid in enumerate(item_ids)}
    for it in items:
        it.position = order[it.id]
    s.flush()
    return list_items(s, cut.id)


# ------------------------- Triage -------------------------


def triage_map(s: Session, video_id: str) -> dict[int, str]:
    """{topic_id: state} for the live; a missing topic id means `open`."""
    rows = s.execute(
        select(TopicTriageRow).where(TopicTriageRow.video_id == video_id)
    ).scalars()
    return {r.topic_id: r.state for r in rows}


def set_triage(s: Session, video_id: str, topic_id: int, state: str) -> None:
    """Write a topic's triage. `open` clears the row; `maybe`/`discarded` upsert.

    `in` is rejected on purpose — membership lives in `cut_items`, never here.
    """
    topic = s.get(TopicRow, topic_id)
    if topic is None or topic.video_id != video_id:
        raise ValueError("topic not found in this live")
    if state == "open":
        s.execute(
            delete(TopicTriageRow).where(
                TopicTriageRow.video_id == video_id,
                TopicTriageRow.topic_id == topic_id,
            )
        )
        return
    if state not in _TRIAGE_STATES:
        raise ValueError("state must be one of: maybe, discarded, open")
    row = s.execute(
        select(TopicTriageRow).where(
            TopicTriageRow.video_id == video_id,
            TopicTriageRow.topic_id == topic_id,
        )
    ).scalars().first()
    if row is None:
        row = TopicTriageRow(video_id=video_id, topic_id=topic_id, state=state)
        s.add(row)
    else:
        row.state = state
    s.flush()


def derive_state(topic_id: int, in_active_cut: set[int], triage: dict[int, str]) -> str:
    """The bench's displayed state, in precedence: item in the active cut -> `in`;
    else the stored triage (`maybe`/`discarded`); else `open`."""
    if topic_id in in_active_cut:
        return "in"
    return triage.get(topic_id, "open")
