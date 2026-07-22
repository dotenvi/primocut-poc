"""Download ONLY the section around a cut item, via yt-dlp `--download-sections`.

The section is an ephemeral intermediate: the caller writes it to a throwaway
temp dir and discards it when the clip is done. We pass `--force-keyframes-at-cuts`
so the section is cut EXACTLY to `[dl_start, dl_end]`: its first frame is the item
window start (`dl_start`) at local time 0, and audio/video stay in sync. Without it
yt-dlp's keyframe stream-copy + stream MERGE mangles the timestamps on YouTube's
AV1+Opus streams — it pads the section and shifts the video off the audio — so the
FFmpeg trim in `encode.py` seeks to the wrong place and yields an empty/broken clip.
The margin on each side guarantees the exact item range sits inside the section;
`encode.py` then re-encodes the frame-exact boundaries at offset `start_s - dl_start`.

Resilience mirrors the audio download (`download/audio.py`): the YouTube
extractor breaks frequently (throttling, "format not available", a bot check),
so a failing section download is retried on transient errors AND falls back
through the configured `player_client` list. yt-dlp's own stderr is CAPTURED and
surfaced in the raised error — without it a failure is just an opaque "exit
status 1" (what the clip row used to store), impossible to act on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..resilience import RetryPolicy, is_transient, retry

logger = get_logger(__name__)

# Invoke yt-dlp through the current interpreter (`python -m yt_dlp`), so it works
# even when the `.venv/bin/yt-dlp` binary is not on PATH (same as download/audio).
YTDLP = [sys.executable, "-m", "yt_dlp"]


def section_bounds(start_s: float, end_s: float, margin_s: float,
                   duration: float) -> tuple[float, float]:
    """The download window [dl_start, dl_end] = the item range widened by the
    margin, clamped to [0, duration] (an unknown duration <= 0 skips the upper
    clamp)."""
    dl_start = max(0.0, start_s - margin_s)
    dl_end = end_s + margin_s
    if duration and duration > 0:
        dl_end = min(duration, dl_end)
    return dl_start, dl_end


def build_section_cmd(url: str, out_template: str, dl_start: float, dl_end: float,
                      extra: list[str] | None = None) -> list[str]:
    """The exact yt-dlp command for the section download.

    `bv*+ba/b` keeps the original resolution (best video + best audio, merged).
    `--force-keyframes-at-cuts` re-encodes the cut boundaries so the section spans
    EXACTLY `[dl_start, dl_end]` (local time 0 = `dl_start`, A/V synced) instead of
    the timestamp-mangled, padded output the plain keyframe copy + merge produces on
    YouTube's AV1+Opus streams. `extra` carries the per-attempt extractor args (the
    `player_client` fallback), inserted before the URL.
    """
    return [
        *YTDLP,
        "-f", "bv*+ba/b",
        "--download-sections", f"*{dl_start:.3f}-{dl_end:.3f}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mkv",
        "--no-warnings",
        *(extra or []),
        "-o", out_template,
        url,
    ]


def _retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        attempts=settings.retry_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )


def _run_section(runner, cmd: list[str]) -> None:
    """Run one yt-dlp invocation, CAPTURING its output so a failure carries the
    real reason (raised as a RuntimeError with the stderr tail) instead of the
    opaque `CalledProcessError` repr."""
    proc = runner(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        tail = " | ".join(
            (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        ) or "no output"
        raise RuntimeError(f"yt-dlp exited {proc.returncode}: {tail}")


def download_section(
    url: str,
    out_dir: Path,
    *,
    start_s: float,
    end_s: float,
    margin_s: float,
    duration: float,
    settings: Settings | None = None,
    runner=subprocess.run,
) -> Path:
    """Download the section into `out_dir` and return the produced file path.

    Resilience: transient retry (network) + a `player_client` fallback (the
    YouTube extractor breaks frequently). Every attempt is tried in turn; the
    final failure carries yt-dlp's own message so the clip error is actionable.
    """
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    dl_start, dl_end = section_bounds(start_s, end_s, margin_s, duration)
    out_template = str(out_dir / "section.%(ext)s")
    logger.info("Downloading section [%.3f, %.3f] for the clip…", dl_start, dl_end)

    clients = [c.strip() for c in settings.ytdlp_player_clients.split(",") if c.strip()]
    attempts = [["--extractor-args", f"youtube:player_client={c}"] for c in clients] or [[]]
    policy = _retry_policy(settings)

    last_exc: Exception | None = None
    for extra in attempts:
        cmd = build_section_cmd(url, out_template, dl_start, dl_end, extra)
        try:
            retry(
                lambda c=cmd: _run_section(runner, c),
                policy=policy, is_retryable=is_transient, label="section download",
            )
            return _find_section(out_dir)
        except Exception as exc:  # yt-dlp broke for this client → try the next one
            last_exc = exc
            logger.warning("Section download failed (extractor-args=%s): %s",
                           extra or "default", exc)
    raise RuntimeError(
        f"Failed to download the clip section after {len(attempts)} yt-dlp "
        "configuration(s). Try updating yt-dlp or adjusting "
        f"PRIMOCUT_YTDLP_PLAYER_CLIENTS. Last error: {last_exc}"
    )


def _find_section(out_dir: Path) -> Path:
    matches = [p for p in out_dir.glob("section.*") if p.is_file()]
    if not matches:
        raise RuntimeError("yt-dlp did not produce the section file.")
    return matches[0]
