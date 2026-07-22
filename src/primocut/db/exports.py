"""DAO for Phase 3 cut exports (one row per cut, tracking its latest export).

Pure DB access. The row is what the UI polls to know when the zip is ready; the
package files themselves live on disk under `exports_dir` (history kept as
timestamped dirs). One `ExportRow` per cut, upserted on each export.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ExportRow


def get_export(s: Session, cut_id: int) -> ExportRow | None:
    return s.execute(
        select(ExportRow).where(ExportRow.cut_id == cut_id)
    ).scalars().first()


def reset_to_queued(
    s: Session,
    *,
    video_id: str,
    cut_id: int,
    item_count: int,
    skip_master: bool,
    upscaled: bool,
) -> ExportRow:
    """Create the cut's export row, or reset the existing one to `queued`."""
    row = get_export(s, cut_id)
    if row is None:
        row = ExportRow(cut_id=cut_id)
        s.add(row)
    row.video_id = video_id
    row.item_count = item_count
    row.skip_master = skip_master
    row.upscaled = upscaled
    row.state = "queued"
    row.error = ""
    row.package_rel_path = ""
    row.zip_rel_path = ""
    s.flush()
    return row


def set_state(s: Session, cut_id: int, state: str, **fields) -> None:
    row = get_export(s, cut_id)
    if row is None:
        return
    row.state = state
    for k, v in fields.items():
        setattr(row, k, v)
    s.flush()
