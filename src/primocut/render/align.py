"""Surgical WhisperX boundary refinement for a single cut item.

The item's exact `[start, end]` (snapshotted from its topic/range) is nudged to
the nearest word/sentence edge — but ONLY:

- from the SECTION already on disk (the ±margin window yt-dlp fetched for the
  cut): we extract audio for just the item's own window `[start-tol, end+tol]` and
  align that. We NEVER re-download and NEVER re-transcribe the live.
- within `tolerance_s` (default ±3 s): a nearest edge farther than the tolerance
  keeps the original timestamp, so a boundary never runs away, and it never lands
  mid-word (it snaps to a word start / word end, which are word edges).
- under the distributed GPU lock `primocut:gpu`: the wav2vec2 inference shares the
  GPU with transcription, so it is serialized the same way.

Every failure mode is a SILENT fallback to the original `[start, end]`: alignment
not configured, the subprocess unavailable, a timeout, or any error. Refinement is
an improvement, never a blocker (PRIMOCUT.md guideline #12, "degrade, don't
abort"). The heavy alignment runs out-of-process in the isolated align venv
(`align_worker`); here it is just an injectable seam, faked in the tests.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..logging_conf import get_logger

logger = get_logger(__name__)


class AlignmentUnavailable(RuntimeError):
    """Raised when alignment cannot run (no align venv configured, etc.)."""


def _nearest_within(candidates: list[float], target: float, tolerance_s: float) -> float:
    """The candidate edge nearest to `target` within `tolerance_s`, else `target`."""
    best: float | None = None
    best_d: float | None = None
    for c in candidates:
        d = abs(c - target)
        if d <= tolerance_s and (best_d is None or d < best_d):
            best, best_d = c, d
    return best if best is not None else target


def snap_bounds(
    words: list[dict], start_s: float, end_s: float, tolerance_s: float
) -> tuple[float, float]:
    """Snap `[start_s, end_s]` to the nearest word edges within `tolerance_s`.

    `start` snaps to the nearest word START, `end` to the nearest word END — both
    word edges, so the cut never lands inside a word. If the nearest edge is
    farther than the tolerance, that boundary is left untouched. If snapping would
    invert the range, both originals are kept.
    """
    if not words:
        return start_s, end_s
    new_start = _nearest_within([w["start"] for w in words], start_s, tolerance_s)
    new_end = _nearest_within([w["end"] for w in words], end_s, tolerance_s)
    if new_start >= new_end:  # a degenerate snap → keep the safe originals
        return start_s, end_s
    return new_start, new_end


def extract_window_audio(
    section: Path,
    out: Path,
    *,
    local_start: float,
    local_dur: float,
    runner: Callable = subprocess.run,
) -> Path:
    """Extract 16 kHz mono WAV for ONLY the item window from the local section.

    `local_start`/`local_dur` are relative to the section file (t=0 = its first
    frame). `-vn` drops the video, so this is cheap. It reads the section already
    on disk — never the network, never the whole file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{local_start:.3f}", "-i", str(section), "-t", f"{local_dur:.3f}",
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out),
        ],
        check=True, stdin=subprocess.DEVNULL,
    )
    return out


def run_align_subprocess(
    wav: Path,
    *,
    language: str,
    settings: Settings,
    timeout_s: float,
    runner: Callable = subprocess.run,
) -> list[dict]:
    """Invoke the align venv's `align_worker` and parse its word JSON.

    Raises `AlignmentUnavailable` when no align venv is configured; a subprocess
    timeout raises `subprocess.TimeoutExpired` and a non-zero exit raises
    `CalledProcessError` — all caught by `refine_bounds` as a silent fallback."""
    if not settings.align_python:
        raise AlignmentUnavailable("PRIMOCUT_ALIGN_PYTHON is not set")
    proc = runner(
        [settings.align_python, "-m", "primocut.render.align_worker",
         str(wav), "--language", language],
        capture_output=True, text=True, check=True, timeout=timeout_s,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(proc.stdout or "{}").get("words", [])


def refine_bounds(
    section: Path,
    *,
    s0: float,
    start_s: float,
    end_s: float,
    language: str,
    settings: Settings,
    workdir: Path,
    gpu_lock=None,
    extract_fn: Callable = extract_window_audio,
    align_fn: Callable = run_align_subprocess,
) -> tuple[float, float]:
    """Return the refined `[start, end]`, or the originals on any failure.

    Extracts the item window from `section`, aligns it under the GPU lock, and
    snaps the boundaries. `s0` is the section's `ffprobe` start_time (its timeline
    origin); the window audio's t=0 maps to `window_origin` on the live timeline.
    """
    tolerance_s = settings.align_tolerance_s
    # Item window on the live timeline, clamped to the section's start.
    window_origin = max(s0, start_s - tolerance_s)
    local_start = window_origin - s0
    local_dur = (end_s + tolerance_s) - window_origin
    if local_dur <= 0:  # nothing to align (degenerate range)
        return start_s, end_s

    try:
        wav = extract_fn(
            section, Path(workdir) / "align.wav",
            local_start=local_start, local_dur=local_dur,
        )
        # The GPU lock wraps ONLY the inference (extraction is CPU/ffmpeg).
        with (gpu_lock or contextlib.nullcontext()):
            words_rel = align_fn(
                wav, language=language, settings=settings,
                timeout_s=settings.align_timeout_s,
            )
        # Map window-relative timestamps back onto the live timeline.
        words = [
            {"text": w.get("text", ""),
             "start": float(w["start"]) + window_origin,
             "end": float(w["end"]) + window_origin}
            for w in words_rel
            if w.get("start") is not None and w.get("end") is not None
        ]
        r_start, r_end = snap_bounds(words, start_s, end_s, tolerance_s)
        if (r_start, r_end) != (start_s, end_s):
            logger.info(
                "align: refined bounds [%.3f, %.3f] → [%.3f, %.3f] (±%.1fs)",
                start_s, end_s, r_start, r_end, tolerance_s,
            )
        else:
            logger.info("align: no edge within ±%.1fs; kept original bounds.", tolerance_s)
        return r_start, r_end
    except Exception as exc:  # silent fallback — refinement never blocks a render
        logger.warning(
            "align: unavailable/failed (%s); keeping original bounds [%.3f, %.3f].",
            exc, start_s, end_s,
        )
        return start_s, end_s
