"""Deterministic candidate-frame extraction from a rendered clip — FFmpeg ONLY.

There is NO LLM anywhere in this module (guideline 9): extraction is free and
runs on an explicit but cost-free user action. Ranking the extracted frames
against the thumbnail brief is a SEPARATE, opt-in vision pass that lives in
`packaging.generate` — never here.

Strategy: take scene-change frames first (a cut usually has a few), then fill up
to the requested count with evenly-spaced samples, so even a visually flat clip
still yields the requested number. The frames are written as `frame-NNN.jpg` under
the clip's own frames dir and are pay-once by presence (re-extraction is skipped
unless `regenerate`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..logging_conf import get_logger

logger = get_logger(__name__)


def build_scene_cmd(clip: Path, out_pattern: str, threshold: float) -> list[str]:
    """FFmpeg command selecting scene-change frames above `threshold` as JPEGs."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(clip),
        "-vf", f"select='gt(scene,{threshold:.3f})'",
        "-vsync", "vfr", "-frames:v", "50", "-q:v", "3",
        out_pattern,
    ]


def build_sample_cmd(clip: Path, out: Path, timestamp: float) -> list[str]:
    """FFmpeg command grabbing a single frame at `timestamp` (fast seek)."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{timestamp:.3f}", "-i", str(clip),
        "-frames:v", "1", "-q:v", "3", str(out),
    ]


def probe_duration(clip: Path, *, runner=subprocess.run) -> float:
    """The clip's duration in seconds (0.0 if unknown)."""
    proc = runner(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True, check=True,
    )
    try:
        return float((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return 0.0


def _sample_timestamps(duration: float, count: int) -> list[float]:
    """`count` evenly-spaced interior timestamps of a clip of `duration`."""
    if count <= 0:
        return []
    if duration <= 0:
        return [0.0] * count
    return [duration * (i + 1) / (count + 1) for i in range(count)]


def extract_frames(
    clip_path: Path,
    out_dir: Path,
    *,
    count: int,
    scene_threshold: float,
    regenerate: bool = False,
    runner=subprocess.run,
    probe_fn=probe_duration,
) -> list[str]:
    """Extract `count` candidate frames from `clip_path` into `out_dir`.

    Returns the ordered frame filenames (`frame-NNN.jpg`). Pay-once: an existing
    full set is returned untouched unless `regenerate`. NO LLM is involved.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("frame-*.jpg"))
    if not regenerate and len(existing) >= count:
        return [p.name for p in existing[:count]]

    # Clear any partial/old set before re-extracting.
    for p in out_dir.glob("frame-*.jpg"):
        p.unlink(missing_ok=True)
    for p in out_dir.glob("scene-*.jpg"):
        p.unlink(missing_ok=True)

    # Pass 1: scene-change candidates (best-effort — a flat clip yields none).
    scene_pattern = str(out_dir / "scene-%03d.jpg")
    try:
        runner(build_scene_cmd(clip_path, scene_pattern, scene_threshold),
               check=True, stdin=subprocess.DEVNULL)
    except Exception as exc:  # scene detection is optional; samples still fill in
        logger.warning("frames: scene detection failed (%s) — using samples only", exc)
    scenes = sorted(out_dir.glob("scene-*.jpg"))[:count]

    frames: list[Path] = []
    for i, scene in enumerate(scenes):
        dest = out_dir / f"frame-{i + 1:03d}.jpg"
        scene.rename(dest)
        frames.append(dest)

    # Pass 2: fill the remainder with evenly-spaced samples.
    remaining = count - len(frames)
    if remaining > 0:
        duration = probe_fn(clip_path, runner=runner)
        for ts in _sample_timestamps(duration, remaining):
            dest = out_dir / f"frame-{len(frames) + 1:03d}.jpg"
            runner(build_sample_cmd(clip_path, dest, ts),
                   check=True, stdin=subprocess.DEVNULL)
            frames.append(dest)

    # Drop any leftover scene files not promoted to a frame.
    for p in out_dir.glob("scene-*.jpg"):
        p.unlink(missing_ok=True)

    return [p.name for p in frames]
