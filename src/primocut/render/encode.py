"""Fine cut of a downloaded section into the final MP4, with FFmpeg.

RE-ENCODE is the default (NVENC `h264_nvenc`, falling back to `libx264`): it
produces frame-exact start/end boundaries, which a keyframe stream copy cannot.
`-c copy` is available only via the explicit `fast_copy` flag, documented as
keyframe-imprecise. Output is MP4 (H.264 + AAC) at the original resolution.

The section is cut to exactly `[dl_start, dl_end]` (`--force-keyframes-at-cuts`),
so its local time 0 is the source time `dl_start` (= `S0`); the trim point inside
the section is `item.start_s - S0`. `-ss` before `-i` is a fast seek; the re-encode
makes the cut frame-exact.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

from ..config import Settings
from ..logging_conf import get_logger

logger = get_logger(__name__)

# The NVENC failure fallback, in order. A requested "libx264" or "copy" profile
# has no fallback (the user asked for it explicitly).
_NVENC_FALLBACK = ["nvenc", "libx264"]


def slugify(text: str, maxlen: int = 40) -> str:
    """ASCII, lowercase, hyphenated slug for a clip filename; never empty."""
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return s[:maxlen].strip("-") or "clip"


def clip_filename(position: int, title: str) -> str:
    """`NNN-slug.mp4`, NNN = 1-based position in the composition, zero-padded 3."""
    return f"{position + 1:03d}-{slugify(title)}.mp4"


def nvenc_listed(runner=subprocess.run) -> bool:
    """Whether FFmpeg lists the `h264_nvenc` encoder at all (a cheap pre-check)."""
    try:
        proc = runner(["ffmpeg", "-hide_banner", "-encoders"],
                      capture_output=True, text=True)
    except FileNotFoundError:
        return False
    return "h264_nvenc" in (proc.stdout or "")


def build_encode_cmd(section: Path, out: Path, *, local_start: float,
                     local_dur: float, encoder: str, settings: Settings) -> list[str]:
    """The exact FFmpeg command for a fine cut with the given encoder profile."""
    base = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-stats", "-y",
        # `-ss` before `-i`: fast seek; the re-encode makes it frame-exact. On the
        # timestamp-preserving section, local_start is item.start_s - S0.
        "-ss", f"{local_start:.3f}", "-i", str(section), "-t", f"{local_dur:.3f}",
    ]
    if encoder == "copy":
        # Keyframe-imprecise on purpose (opt-in `--fast-copy`): no re-encode.
        return [*base, "-c", "copy", "-movflags", "+faststart", str(out)]
    if encoder == "libx264":
        vcodec = ["-c:v", "libx264", "-preset", "medium",
                  "-crf", str(settings.render_x264_crf), "-pix_fmt", "yuv420p"]
    else:  # nvenc
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                  "-rc", "vbr", "-cq", str(settings.render_nvenc_cq), "-b:v", "0",
                  "-pix_fmt", "yuv420p"]
    return [
        *base, *vcodec,
        "-c:a", "aac", "-b:a", settings.render_audio_bitrate,
        "-movflags", "+faststart", str(out),
    ]


def encode_clip(
    section: Path,
    out: Path,
    *,
    local_start: float,
    local_dur: float,
    profile: str,
    settings: Settings,
    runner=subprocess.run,
) -> tuple[str, str | None]:
    """Re-encode (or copy) the section into `out`. Returns (encoder_used, warning).

    NVENC is tried first for the "nvenc" profile; a failure falls back to
    libx264 with a slowness warning. "libx264" and "copy" have no fallback.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if profile == "copy":
        runner(build_encode_cmd(section, out, local_start=local_start,
                                local_dur=local_dur, encoder="copy", settings=settings),
               check=True, stdin=subprocess.DEVNULL)
        return "copy", None

    order = _NVENC_FALLBACK if profile == "nvenc" else [profile]
    last_exc: Exception | None = None
    for i, encoder in enumerate(order):
        try:
            runner(build_encode_cmd(section, out, local_start=local_start,
                                    local_dur=local_dur, encoder=encoder, settings=settings),
                   check=True, stdin=subprocess.DEVNULL)
            warning = None
            if encoder != profile:
                warning = (f"NVENC unavailable — fell back to {encoder} "
                           "(slower software encode).")
                logger.warning(warning)
            return encoder, warning
        except Exception as exc:  # this encoder failed → try the fallback
            last_exc = exc
            logger.warning("Encoder %s failed: %s", encoder, exc)
            if i + 1 < len(order):
                logger.info("Falling back to %s.", order[i + 1])
    raise RuntimeError(
        f"Failed to encode the clip with {order}. Last error: {last_exc}"
    )
