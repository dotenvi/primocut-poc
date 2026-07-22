"""Tests for Phase 3 cut export (packaging clips into a deliverable bundle).

FFmpeg is faked (no re-encode, no GPU): the master runner records the command and
writes a stub file. Covers the package structure, order/naming, the concat master
STREAM COPY command, per-clip SRT offsets (topic and manual range, including a
WhisperX-refined boundary), the manifests (with and without packaging), the
skip-master flag, the 1440p NVENC upscale command, and the un-rendered behavior
(fail by default, render_missing renders first).
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from primocut.config import Settings
from primocut.db import clips as clips_repo
from primocut.db import competitors as crepo
from primocut.db import cuts as cuts_repo
from primocut.db import exports as exports_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Segment, Topic, Transcript, Video, VideoStatus
from primocut.render import package as pkg
from primocut.worker.export_jobs import export_cut
from primocut.worker.render_jobs import render_clips


# ------------------------- pure helpers -------------------------


def test_srt_timestamp_format():
    assert pkg._srt_ts(0.0) == "00:00:00,000"
    assert pkg._srt_ts(3661.5) == "01:01:01,500"
    assert pkg._srt_ts(-5.0) == "00:00:00,000"


def test_srt_offsets_to_clip_start():
    # Segments in LIVE time; the clip is [100, 160].
    segs = [
        Segment(index=0, start=95.0, end=105.0, text="early"),
        Segment(index=1, start=110.0, end=120.0, text="middle"),
        Segment(index=2, start=200.0, end=210.0, text="outside"),
    ]
    srt = pkg.srt_for_clip(segs, 100.0, 160.0)
    # "outside" (200s) is excluded; the two overlapping cues are offset to 0-base.
    assert "outside" not in srt
    assert srt.startswith("1\n00:00:00,000 --> 00:00:05,000\nearly")
    assert "2\n00:00:10,000 --> 00:00:20,000\nmiddle" in srt


def test_srt_empty_when_no_overlap():
    segs = [Segment(index=0, start=0.0, end=5.0, text="a")]
    assert pkg.srt_for_clip(segs, 100.0, 160.0) == ""


def test_build_concat_file_relative_names():
    txt = pkg.build_concat_file(["001-a.mp4", "002-b.mp4"])
    assert txt == "file '001-a.mp4'\nfile '002-b.mp4'\n"


def test_master_cmd_copy_has_no_video_reencode():
    st = Settings()
    cmd = pkg.build_master_cmd(Path("c.txt"), Path("m.mp4"),
                               encoder="copy", height=0, settings=st)
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    assert cmd[cmd.index("-c") + 1] == "copy"
    # A stream copy: no re-encode, no scaling.
    assert "-c:v" not in cmd and "-vf" not in cmd and "h264_nvenc" not in cmd


def test_master_cmd_upscale_is_nvenc_scale():
    st = Settings()
    cmd = pkg.build_master_cmd(Path("c.txt"), Path("m.mp4"),
                               encoder="nvenc", height=1440, settings=st)
    assert "h264_nvenc" in cmd
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:1440:flags=lanczos"
    assert "aac" in cmd and "+faststart" in cmd


def test_clip_export_name():
    assert pkg.clip_export_name(0, "Olá Mundo", "mp4") == "001-ola-mundo.mp4"
    assert pkg.clip_export_name(11, "T", "srt") == "012-t.srt"


# ------------------------- job (fakes) -------------------------


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed(db, video_id="vid00000001", duration=600.0):
    """A live with a transcript, two topic items and one manual-range item."""
    with db.session() as s:
        pid = crepo.create_project(s, "N").id
        repo.upsert_video(s, Video(
            id=video_id, url=f"https://y/{video_id}", title="My Live", channel="C",
            duration=duration, status=VideoStatus.DONE, project_id=pid,
        ))
        # Segments every 10s from 90 to 370 (cover the two topics + the manual range).
        segs = [Segment(index=i, start=float(t), end=float(t + 10),
                        text=f"seg at {t}")
                for i, t in enumerate(range(90, 380, 10))]
        repo.save_transcript(s, Transcript(
            video_id=video_id, language="pt", model="m", segments=segs))
        repo.save_topics(s, video_id, [
            Topic(title="Alpha", summary="", start=100.0, end=160.0, score=0.5),
            Topic(title="Beta", summary="", start=200.0, end=260.0, score=0.5),
        ])
        tids = [t.id for t in repo.get_topic_rows(s, video_id)]
        cut = cuts_repo.ensure_default_cut(s, video_id)
        for t in tids:
            cuts_repo.add_item(s, cut, duration=duration, topic_id=t)
        # A manual range item [300, 360].
        cuts_repo.add_item(s, cut, duration=duration, start_s=300.0, end_s=360.0)
        cut_id = cut.id
    return pid, video_id, cut_id


def _render_fakes():
    def download(url, out_dir, *, start_s, end_s, margin_s, duration, settings=None):
        p = Path(out_dir) / "section.mkv"
        p.write_bytes(b"section")
        return p

    def encode(section, dest, *, local_start, local_dur, profile, settings):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"clip-bytes")
        return (profile, None)

    return download, encode


def _render_all(db, settings, video_id, cut_id, item_ids=None):
    dl, enc = _render_fakes()
    return render_clips(
        video_id, cut_id, item_ids, settings=settings, database=db,
        download_section_fn=dl, encode_clip_fn=enc,
    )


class _MasterRunner:
    """Records the FFmpeg master command and writes a stub master file."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"master")
        return None


def _settings(tmp_path):
    return Settings(clips_dir=tmp_path / "clips", exports_dir=tmp_path / "exports")


_NOW = datetime(2026, 7, 20, 22, 54, 0, tzinfo=timezone.utc)


def test_package_structure_order_and_naming(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    res = export_cut(vid, cid, settings=st, database=db, master_runner=mr,
                     now_fn=lambda: _NOW)
    assert res["state"] == "done" and res["items"] == 3
    pkg_dir = Path(res["package_dir"])
    # Timestamped package dir under exports/<vid>/<cut>/.
    assert pkg_dir.parent == st.exports_dir / vid / str(cid)
    assert pkg_dir.name.startswith("cut-1-20260720-225400")

    # Ordered NNN-slug.mp4 + matching .srt for every item.
    names = sorted(p.name for p in pkg_dir.iterdir())
    assert "001-alpha.mp4" in names and "001-alpha.srt" in names
    assert "002-beta.mp4" in names and "002-beta.srt" in names
    # The manual range gets a neutral slug.
    assert "003-manual-range.mp4" in names
    assert (pkg_dir / "master.mp4").exists()
    assert (pkg_dir / "manifest.json").exists()
    assert (pkg_dir / "manifest.md").exists()
    # concat.txt is not part of the deliverable.
    assert not (pkg_dir / "concat.txt").exists()

    # The originals under clips_dir are untouched (export copies, never moves).
    assert (st.clips_dir / vid / str(cid) / "001-alpha.mp4").exists()

    # The zip mirrors the package.
    zip_path = Path(res["zip_path"])
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as z:
        assert "001-alpha.mp4" in z.namelist()
        assert "master.mp4" in z.namelist()


def test_master_is_concat_stream_copy(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    export_cut(vid, cid, settings=st, database=db, master_runner=mr, now_fn=lambda: _NOW)
    assert len(mr.calls) == 1
    cmd = mr.calls[0]
    # Concat demuxer + stream copy: NO re-encode.
    assert cmd[cmd.index("-f") + 1] == "concat"
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "-c:v" not in cmd and "h264_nvenc" not in cmd and "libx264" not in cmd


def test_srt_offsets_for_topic_and_manual_range(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    res = export_cut(vid, cid, settings=st, database=db, master_runner=mr,
                     now_fn=lambda: _NOW)
    pkg_dir = Path(res["package_dir"])
    # Topic [100,160]: the first cue (seg at 100) starts at 0 in the clip's base.
    alpha = (pkg_dir / "001-alpha.srt").read_text()
    assert "00:00:00,000 --> 00:00:10,000\nseg at 100" in alpha
    # Manual range [300,360]: same 0-based offset (seg at 300 → 0).
    manual = (pkg_dir / "003-manual-range.srt").read_text()
    assert "00:00:00,000 --> 00:00:10,000\nseg at 300" in manual


def test_srt_uses_refined_render_bounds(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    # Simulate a WhisperX shift: the Alpha clip was actually encoded at [110, 160].
    with db.session() as s:
        items = cuts_repo.list_items(s, cid)
        clip = clips_repo.clip_for_item(s, items[0].id)
        clips_repo.set_state(s, clip.id, "done", render_start_s=110.0, render_end_s=160.0)

    mr = _MasterRunner()
    res = export_cut(vid, cid, settings=st, database=db, master_runner=mr,
                     now_fn=lambda: _NOW)
    pkg_dir = Path(res["package_dir"])
    alpha = (pkg_dir / "001-alpha.srt").read_text()
    # With start refined to 110, "seg at 110" is now the 0-based first cue.
    assert alpha.startswith("1\n00:00:00,000 --> 00:00:10,000\nseg at 110")
    # And the manifest reports the refined bounds.
    data = json.loads((pkg_dir / "manifest.json").read_text())
    assert data["items"][0]["start_s"] == 110.0


def test_manifest_contents_and_empty_packaging(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    res = export_cut(vid, cid, settings=st, database=db, master_runner=mr,
                     now_fn=lambda: _NOW)
    pkg_dir = Path(res["package_dir"])
    data = json.loads((pkg_dir / "manifest.json").read_text())
    assert data["live"]["video_id"] == vid and data["live"]["title"] == "My Live"
    assert data["master"] == {"filename": "master.mp4", "upscaled": False,
                              "resolution": "original"}
    assert [it["mp4"] for it in data["items"]] == [
        "001-alpha.mp4", "002-beta.mp4", "003-manual-range.mp4"]
    assert data["items"][2]["kind"] == "manual"
    # Packaging fields exist but are empty until the AI packaging generates them.
    assert all(it["packaging"]["titles"] == [] and it["packaging"]["frames"] == []
               and it["packaging"]["thumbnail_brief"] == ""
               for it in data["items"])
    md = (pkg_dir / "manifest.md").read_text()
    assert "My Live" in md and "&t=100" in md  # clickable live timestamp
    assert "## Packaging" not in md  # nothing generated yet


def test_skip_master(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    res = export_cut(vid, cid, skip_master=True, settings=st, database=db,
                     master_runner=mr, now_fn=lambda: _NOW)
    pkg_dir = Path(res["package_dir"])
    assert not (pkg_dir / "master.mp4").exists()
    assert len(mr.calls) == 0  # FFmpeg not invoked at all
    data = json.loads((pkg_dir / "manifest.json").read_text())
    assert data["master"] is None
    # The clips + SRTs are still there.
    assert (pkg_dir / "001-alpha.mp4").exists()


def test_upscale_1440p_emits_nvenc_scale_command(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    mr = _MasterRunner()
    res = export_cut(vid, cid, upscale_1440p=True, settings=st, database=db,
                     master_runner=mr, now_fn=lambda: _NOW)
    assert res["upscaled"] is True
    cmd = mr.calls[0]
    assert "h264_nvenc" in cmd
    assert cmd[cmd.index("-vf") + 1] == "scale=-2:1440:flags=lanczos"
    data = json.loads((Path(res["package_dir"]) / "manifest.json").read_text())
    assert data["master"]["resolution"] == "1440p" and data["master"]["upscaled"]


def test_upscale_conflicts_with_skip_master(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    res = export_cut(vid, cid, upscale_1440p=True, skip_master=True,
                     settings=st, database=db, master_runner=_MasterRunner(),
                     now_fn=lambda: _NOW)
    assert res["state"] == "error" and "master" in res["error"]


def test_unrendered_items_fail_by_default(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    # No render at all → every item is missing.
    res = export_cut(vid, cid, settings=st, database=db,
                     master_runner=_MasterRunner(), now_fn=lambda: _NOW)
    assert res["state"] == "error"
    assert "not rendered" in res["error"]
    # No package dir was produced.
    assert not (st.exports_dir / vid).exists()
    with db.session() as s:
        assert exports_repo.get_export(s, cid).state == "error"


def test_render_missing_renders_then_packages(tmp_path):
    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)

    def render_fn(video_id, cut_id, item_ids, *, settings, database):
        _render_all(database, settings, video_id, cut_id, item_ids)

    mr = _MasterRunner()
    res = export_cut(vid, cid, render_missing=True, settings=st, database=db,
                     render_fn=render_fn, master_runner=mr, now_fn=lambda: _NOW)
    assert res["state"] == "done" and res["items"] == 3
    assert (Path(res["package_dir"]) / "001-alpha.mp4").exists()


def test_manifest_incorporates_ai_packaging(tmp_path):
    """Titles + brief + copied candidate frames land in the manifest; the originals
    under clips_dir/frames stay put (copied, never moved)."""
    from primocut.db import packaging as pkg_repo
    from primocut.worker.packaging_jobs import frames_dir_for

    db = _db(tmp_path)
    _, vid, cid = _seed(db)
    st = _settings(tmp_path)
    _render_all(db, st, vid, cid)

    with db.session() as s:
        item_ids = [it.id for it in cuts_repo.list_items(s, cid)]
        first = item_ids[0]
        pkg_repo.upsert_packaging(s, cut_item_id=first, cut_id=cid, kind="title",
            payload={"titles": [
                {"title": "Título vencedor", "length_chars": 15,
                 "patterns_applied": [{"kind": "differential", "reference": "D1"}],
                 "rationale": "shock"}]}, content_hash="h")
        pkg_repo.upsert_packaging(s, cut_item_id=first, cut_id=cid, kind="thumb_brief",
            payload={"composition": {"face_count": 1, "expression": "shock",
                                     "framing": "single"},
                     "text": {"word_count": 2, "suggestion": "OLHA ISSO"},
                     "colors": {"palette": ["red"], "contrast": "high"},
                     "graphics": {"arrow": True, "circle_highlight": False, "note": ""},
                     "references": [], "pattern_notes": "D1"}, content_hash="h2")
        pkg_repo.upsert_packaging(s, cut_item_id=first, cut_id=cid, kind="frame_ranking",
            payload={"rankings": [], "best_frame": 1,
                     "best_filename": "frame-002.jpg"}, content_hash="")

    # Two candidate frames extracted for the first item (position 0) on disk.
    fdir = frames_dir_for(st, vid, cid, 0)
    fdir.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (fdir / f"frame-{i + 1:03d}.jpg").write_bytes(b"\xff\xd8\xff")

    mr = _MasterRunner()
    res = export_cut(vid, cid, settings=st, database=db, master_runner=mr,
                     now_fn=lambda: _NOW)
    assert res["state"] == "done"
    pkg_dir = Path(res["package_dir"])

    data = json.loads((pkg_dir / "manifest.json").read_text())
    first_item = data["items"][0]
    assert first_item["packaging"]["titles"] == ["Título vencedor"]
    assert first_item["packaging"]["titles_detail"][0]["patterns_applied"][0]["reference"] == "D1"
    assert "shock" in first_item["packaging"]["thumbnail_brief"]
    assert first_item["packaging"]["thumbnail_brief_detail"]["text"]["suggestion"] == "OLHA ISSO"
    # The frames are copied into the bundle under NNN-frames/, best flagged.
    assert first_item["packaging"]["frames"] == ["001-frames/frame-001.jpg",
                                                 "001-frames/frame-002.jpg"]
    assert first_item["packaging"]["best_frame"] == "001-frames/frame-002.jpg"
    assert (pkg_dir / "001-frames" / "frame-001.jpg").exists()
    # The original frames under clips_dir are untouched.
    assert (fdir / "frame-001.jpg").exists()

    # An item without packaging stays present-but-empty.
    assert data["items"][1]["packaging"]["titles"] == []

    # The Markdown manifest gained a Packaging section with the title.
    md = (pkg_dir / "manifest.md").read_text()
    assert "## Packaging" in md and "Título vencedor" in md
