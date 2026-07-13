"""Windowing of long transcripts with overlap (technical guideline #3).

3-4h lives exceed the LLM's useful context. We partition the segments into
windows by target duration, with overlap, keeping the GLOBAL segment INDICES —
essential for the LLM to select spans and for later consolidation.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..models import Segment, Transcript, format_timestamp


class Window(BaseModel):
    start_index: int
    end_index: int
    segments: list[Segment]

    def indexed_text(self) -> str:
        return "\n".join(
            f"[{s.index}] {format_timestamp(s.start)} {s.text}" for s in self.segments
        )


def make_windows(
    transcript: Transcript,
    *,
    target_seconds: float,
    overlap_seconds: float,
) -> list[Window]:
    """Split the segments into windows [t, t+target) sliding by (target-overlap)."""
    segments = transcript.segments
    if not segments:
        return []

    duration = transcript.duration
    step = max(target_seconds - overlap_seconds, 1.0)

    windows: list[Window] = []
    win_start = 0.0
    while win_start < duration:
        win_end = win_start + target_seconds
        chunk = [s for s in segments if win_start <= s.start < win_end]
        if chunk:
            windows.append(
                Window(
                    start_index=chunk[0].index,
                    end_index=chunk[-1].index,
                    segments=chunk,
                )
            )
        if win_end >= duration:
            break
        win_start += step

    return windows
