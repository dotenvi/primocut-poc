"""Phase 3 cut export: assemble a cut's rendered clips into a deliverable bundle.

Pure, side-effect-light helpers (SRT text, the concat file, the master FFmpeg
command, the manifests) plus thin subprocess wrappers behind a `runner` seam, so
the tests can fake FFmpeg entirely — no re-encode, no GPU. The export NEVER
mutates the originals under `clips_dir`: the clip MP4s are COPIED into the package.

The master is a concatenation of the clips IN ORDER. Because every clip came out
of the same render profile (same codec/params/resolution), the default master is
a concat-demuxer STREAM COPY (`-c copy`) — seconds, no re-encode. `--upscale-1440p`
is the one path that re-encodes: it scales the concatenated master to 1440p via
NVENC (libx264 fallback), because YouTube assigns better codecs to >=1440p uploads.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import Settings
from ..logging_conf import get_logger
from ..models import format_timestamp
from .encode import slugify

logger = get_logger(__name__)

# The NVENC failure fallback for the upscale re-encode, mirroring encode.py.
_NVENC_FALLBACK = ["nvenc", "libx264"]


# ------------------------- SRT -------------------------


def _srt_ts(seconds: float) -> str:
    """`HH:MM:SS,mmm` — the SubRip timestamp format (comma before milliseconds)."""
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_for_clip(segments, clip_start: float, clip_end: float) -> str:
    """Build the SRT for a clip from the live's transcript segments.

    Each overlapping segment is shifted by the clip's start (so the clip's own
    time base begins at 0) and clamped to `[0, clip_end - clip_start]`. Works
    identically for a topic item and a manual range — the offset is always the
    clip's effective start. Segments are dicts/objects with `start`/`end`/`text`.
    Returns an empty string when nothing overlaps (a valid, empty .srt).
    """
    dur = max(0.0, clip_end - clip_start)
    blocks: list[str] = []
    idx = 1
    for seg in segments:
        s = float(_field(seg, "start"))
        e = float(_field(seg, "end"))
        text = str(_field(seg, "text") or "").strip()
        if e <= clip_start or s >= clip_end or not text:
            continue
        ns = max(0.0, s - clip_start)
        ne = min(dur, e - clip_start)
        if ne <= ns:
            continue
        blocks.append(f"{idx}\n{_srt_ts(ns)} --> {_srt_ts(ne)}\n{text}")
        idx += 1
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def _field(seg, name: str):
    """Read `name` from a Segment model or a plain dict."""
    return seg[name] if isinstance(seg, dict) else getattr(seg, name)


# ------------------------- master (concat) -------------------------


def build_concat_file(filenames: list[str]) -> str:
    """The concat-demuxer input listing the clip files (run with `cwd` = the
    package dir, so the names stay relative). Single quotes are escaped."""
    lines = [f"file '{fn.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
             for fn in filenames]
    return "\n".join(lines) + "\n"


def build_master_cmd(concat_path: Path, out: Path, *, encoder: str,
                     height: int, settings: Settings) -> list[str]:
    """The FFmpeg command that builds the master from the concat file.

    `encoder="copy"` is the default: a concat-demuxer STREAM COPY (no re-encode).
    `encoder="nvenc"|"libx264"` re-encodes with a `scale=-2:height` upscale — used
    only by `--upscale-1440p`.
    """
    base = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-stats", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
    ]
    if encoder == "copy":
        # Identical clip profiles make a stream copy valid; this is the seconds-fast
        # default. No scaling here — the resolution stays the original.
        return [*base, "-c", "copy", "-movflags", "+faststart", str(out)]
    vf = ["-vf", f"scale=-2:{height}:flags=lanczos"]
    if encoder == "libx264":
        vcodec = ["-c:v", "libx264", "-preset", "medium",
                  "-crf", str(settings.render_x264_crf), "-pix_fmt", "yuv420p"]
    else:  # nvenc
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                  "-rc", "vbr", "-cq", str(settings.render_nvenc_cq), "-b:v", "0",
                  "-pix_fmt", "yuv420p"]
    return [
        *base, *vf, *vcodec,
        "-c:a", "aac", "-b:a", settings.render_audio_bitrate,
        "-movflags", "+faststart", str(out),
    ]


def concat_master(
    concat_path: Path,
    out: Path,
    *,
    upscale_1440p: bool,
    settings: Settings,
    runner=subprocess.run,
    cwd: Path | None = None,
) -> tuple[str, str | None]:
    """Produce the master MP4. Returns (encoder_used, warning).

    Default: a stream copy (no re-encode). `--upscale-1440p`: an NVENC re-encode
    scaling to `export_upscale_height`, falling back to libx264 on an NVENC failure
    (same contract as the per-clip encode).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    run_kw = {"check": True, "stdin": subprocess.DEVNULL}
    if cwd is not None:
        run_kw["cwd"] = str(cwd)

    if not upscale_1440p:
        runner(build_master_cmd(concat_path, out, encoder="copy",
                                height=0, settings=settings), **run_kw)
        return "copy", None

    height = settings.export_upscale_height
    last_exc: Exception | None = None
    for i, encoder in enumerate(_NVENC_FALLBACK):
        try:
            runner(build_master_cmd(concat_path, out, encoder=encoder,
                                    height=height, settings=settings), **run_kw)
            warning = None
            if encoder != _NVENC_FALLBACK[0]:
                warning = (f"NVENC unavailable — upscaled the master with {encoder} "
                           "(slower software encode).")
                logger.warning(warning)
            return encoder, warning
        except Exception as exc:  # this encoder failed → try the fallback
            last_exc = exc
            logger.warning("Master encoder %s failed: %s", encoder, exc)
    raise RuntimeError(f"Failed to build the upscaled master. Last error: {last_exc}")


# ------------------------- manifests -------------------------


def build_manifest_data(
    *,
    video,
    cut,
    items: list[dict],
    master: dict | None,
    generated_at: str,
) -> dict:
    """The manifest.json payload: live metadata, the cut, the ordered items with
    their FINAL timestamps, and the master info. Packaging fields (titles/brief)
    exist per item and stay empty until the Step 5 AI packaging fills them."""
    return {
        "generated_at": generated_at,
        "live": {
            "video_id": video.id,
            "url": video.url,
            "title": video.title,
            "channel": video.channel,
            "duration_s": float(video.duration or 0.0),
        },
        "cut": {"id": cut.id, "name": cut.name},
        "master": master,
        "items": items,
    }


def render_manifest_md(data: dict) -> str:
    """A human-readable manifest with clickable timestamps into the live."""
    live = data["live"]
    cut = data["cut"]
    lines: list[str] = []
    lines.append(f"# {live['title'] or live['video_id']} — {cut['name']}")
    lines.append("")
    lines.append(f"- **Live**: [{live['url']}]({live['url']})")
    if live["channel"]:
        lines.append(f"- **Channel**: {live['channel']}")
    lines.append(f"- **Exported**: {data['generated_at']}")
    if data["master"]:
        m = data["master"]
        res = m.get("resolution", "original")
        lines.append(f"- **Master**: `{m['filename']}` ({res})")
    else:
        lines.append("- **Master**: skipped")
    lines.append("")
    lines.append("| # | Clip | Title | Start | End | Duration |")
    lines.append("|---|------|-------|-------|-----|----------|")
    for it in data["items"]:
        watch = _watch_url(live["video_id"], it["start_s"])
        title = (it["title"] or "").replace("|", "\\|")
        lines.append(
            f"| {it['position']} | `{it['mp4']}` | [{title}]({watch}) | "
            f"{format_timestamp(it['start_s'])} | {format_timestamp(it['end_s'])} | "
            f"{format_timestamp(it['duration_s'])} |"
        )
    # Packaging (Step 5) — rendered only once titles/brief are actually generated.
    packaged = [it for it in data["items"]
                if it.get("packaging", {}).get("titles")
                or it.get("packaging", {}).get("thumbnail_brief")]
    if packaged:
        lines.append("")
        lines.append("## Packaging")
        for it in packaged:
            lines.append("")
            lines.append(f"### {it['position']}. {it['title']}")
            pk = it["packaging"]
            for t in pk.get("titles", []):
                lines.append(f"- {t}")
            if pk.get("thumbnail_brief"):
                lines.append(f"- **Thumbnail**: {pk['thumbnail_brief']}")
    lines.append("")
    return "\n".join(lines)


def manifest_json_text(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _watch_url(video_id: str, start_s: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={int(start_s)}"


def clip_export_name(position: int, title: str, ext: str) -> str:
    """`NNN-slug.ext`, NNN = 1-based export position (matches the clip naming)."""
    return f"{position + 1:03d}-{slugify(title)}.{ext}"
