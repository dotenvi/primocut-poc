"""Audio download via yt-dlp and normalization to 16kHz mono with FFmpeg.

`fetch_metadata` and `download_audio` are thin wrappers over subprocess, so they
are easy to mock in tests. `extract_video_id` is pure.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Settings, get_settings
from ..logging_conf import get_logger
from ..models import Video, VideoStatus
from ..resilience import RetryPolicy, is_transient, retry

logger = get_logger(__name__)

# Invoke yt-dlp through the current interpreter (`python -m yt_dlp`), so it works
# even when the `.venv/bin/yt-dlp` binary is not on PATH.
YTDLP = [sys.executable, "-m", "yt_dlp"]

# YouTube video IDs are exactly 11 characters from this alphabet.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def extract_video_id(url: str) -> str:
    """Extract the video ID from a YouTube URL (watch?v=, youtu.be, /live/)."""
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.lstrip("/").split("/")[0]
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    # /live/<id> or /embed/<id> formats
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0] in {"live", "embed", "shorts"} and len(parts) > 1:
        return parts[1]
    if parts:
        return parts[-1]
    raise ValueError(f"Could not extract the video ID from: {url}")


def validate_youtube_url(url: str) -> str:
    """Validate the link format and return the canonical video URL.

    Accepts YouTube video links (watch?v=, youtu.be, /live/, /shorts/) and
    returns `https://www.youtube.com/watch?v=<ID>`. Raises ValueError with a
    clear message when the link is not a recognizable YouTube video — which also
    rejects malformed URLs (e.g. with shell escape backslashes).
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Empty URL.")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"The URL must start with http:// or https:// — got: {url!r}")

    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        raise ValueError(
            f"Not a YouTube link: {url!r}. "
            "Use something like https://www.youtube.com/watch?v=VIDEO_ID"
        )

    try:
        video_id = extract_video_id(raw)
    except ValueError:
        video_id = ""

    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError(
            f"Could not find a valid video ID (11 characters) in: {url!r}. "
            "Check that the link is a video (format https://www.youtube.com/watch?v=VIDEO_ID) "
            "and that there are no escape characters (backslashes) pasted by mistake."
        )

    return f"https://www.youtube.com/watch?v={video_id}"


def _retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        attempts=settings.retry_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )


def fetch_metadata(url: str, settings: Settings | None = None) -> Video:
    """Collect metadata via `yt-dlp -J` (without downloading media), with transient retry."""
    settings = settings or get_settings()

    def _run() -> dict:
        proc = subprocess.run(
            [*YTDLP, "-J", "--no-warnings", url],
            capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        )
        return json.loads(proc.stdout)

    data = retry(_run, policy=_retry_policy(settings), is_retryable=is_transient, label="metadata")
    return Video(
        id=data.get("id") or extract_video_id(url),
        url=data.get("webpage_url", url),
        title=data.get("title", "(no title)"),
        channel=data.get("channel") or data.get("uploader", "(unknown)"),
        duration=float(data.get("duration") or 0.0),
        status=VideoStatus.DOWNLOADING,
    )


def download_audio(
    url: str, out_dir: Path, *, video_id: str | None = None, settings: Settings | None = None
) -> Path:
    """Download the audio and normalize it to 16kHz mono WAV. Returns the .wav path.

    Resilience: transient retry (network) + yt-dlp `player_client` fallback
    (the YouTube extractor breaks frequently).
    """
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    vid = video_id or extract_video_id(url)
    src_template = str(out_dir / f"{vid}.src.%(ext)s")
    normalized = out_dir / f"{vid}.16k.wav"

    # Resume: if the normalized WAV already exists, reuse it (avoids re-downloading).
    if normalized.exists() and normalized.stat().st_size > 0:
        logger.info("Reusing already-downloaded audio: %s", normalized)
        return normalized

    clients = [c.strip() for c in settings.ytdlp_player_clients.split(",") if c.strip()]
    attempts = [["--extractor-args", f"youtube:player_client={c}"] for c in clients] or [[]]
    policy = _retry_policy(settings)

    # 1. yt-dlp downloads ONLY the best compressed audio (without converting to WAV):
    #    avoids one ffmpeg pass and a huge intermediate file (several GB).
    last_exc: Exception | None = None
    for extra in attempts:
        try:
            retry(
                lambda e=extra: subprocess.run(
                    [*YTDLP, "-f", "bestaudio/best", "--no-warnings", *e,
                     "-o", src_template, url],
                    check=True, stdin=subprocess.DEVNULL,
                ),
                policy=policy, is_retryable=is_transient, label="audio download",
            )
            break
        except Exception as exc:  # yt-dlp broke for this client → try the next one
            last_exc = exc
            logger.warning("Download failed (extractor-args=%s): %s", extra or "default", exc)
    else:
        raise RuntimeError(
            f"Failed to download the audio after {len(attempts)} yt-dlp configuration(s). "
            "Try updating yt-dlp or adjusting PRIMOCUT_YTDLP_PLAYER_CLIENTS. "
            f"Last error: {last_exc}"
        )

    src = _find_src(out_dir, vid)

    # 2. SINGLE FFmpeg pass: decode + resample to 16kHz mono.
    #    `-stats` shows real-time progress/speed in the log.
    logger.info("Normalizing audio to 16kHz mono (FFmpeg)…")
    subprocess.run(
        # `-nostdin` + stdin=DEVNULL: without this, running in the background (RQ
        # work-horse) ffmpeg tries to read the terminal, gets SIGTTIN and STOPS (T).
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-stats", "-y",
         "-i", str(src), "-ar", "16000", "-ac", "1", str(normalized)],
        check=True, stdin=subprocess.DEVNULL,
    )
    try:  # the compressed file is no longer needed (the cache is the .16k.wav)
        src.unlink()
    except OSError:
        pass
    logger.info("Normalized audio: %s", normalized)
    return normalized


def _find_src(out_dir: Path, vid: str) -> Path:
    matches = [p for p in out_dir.glob(f"{vid}.src.*") if p.is_file()]
    if not matches:
        raise RuntimeError(f"yt-dlp did not produce the audio file for {vid}.")
    return matches[0]
