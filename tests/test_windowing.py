"""Tests for the overlapping windowing over the transcript segments."""

from __future__ import annotations

from primocut.analysis.windowing import make_windows
from primocut.models import Segment, Transcript


def _transcript(n_segments: int, seg_len: float = 10.0) -> Transcript:
    return Transcript(
        video_id="vid",
        language="pt",
        model="m",
        segments=[
            Segment(index=i, start=i * seg_len, end=(i + 1) * seg_len, text=f"seg{i}")
            for i in range(n_segments)
        ],
    )


def test_empty_transcript_yields_no_windows():
    t = Transcript(video_id="v", language="pt", model="m", segments=[])
    assert make_windows(t, target_seconds=120, overlap_seconds=30) == []


def test_single_window_when_shorter_than_target():
    t = _transcript(5)  # 50s total
    wins = make_windows(t, target_seconds=120, overlap_seconds=30)
    assert len(wins) == 1
    assert wins[0].start_index == 0
    assert wins[0].end_index == 4
    assert len(wins[0].segments) == 5


def test_windows_overlap_and_preserve_global_indices():
    t = _transcript(30)  # 300s total
    wins = make_windows(t, target_seconds=120, overlap_seconds=30)
    # step = 90 -> starts at 0,90,180 (the one at 180 already covers up to 300 and ends)
    assert len(wins) == 3
    # first window: starts 0..110 -> indices 0..11
    assert wins[0].start_index == 0 and wins[0].end_index == 11
    # second window starts at 90 -> index 9 (overlap of 9,10,11)
    assert wins[1].start_index == 9
    shared = set(s.index for s in wins[0].segments) & set(s.index for s in wins[1].segments)
    assert shared == {9, 10, 11}


def test_windows_cover_all_segments():
    t = _transcript(30)
    wins = make_windows(t, target_seconds=120, overlap_seconds=30)
    covered = set()
    for w in wins:
        covered.update(s.index for s in w.segments)
    assert covered == set(range(30))


def test_window_indexed_text_uses_global_index():
    t = _transcript(30)
    wins = make_windows(t, target_seconds=120, overlap_seconds=30)
    first_line = wins[1].indexed_text().splitlines()[0]
    assert first_line.startswith("[9]")
