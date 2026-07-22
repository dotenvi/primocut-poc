"""Tests for Phase 3 clip rendering (section download + FFmpeg re-encode + job).

yt-dlp and FFmpeg are fully faked (no network, no GPU). Covers: section margins,
the exact re-encode command and its frame-exact timestamps, the NVENC→libx264
fallback, the pay-once cache by parameters, per-item failure isolation,
naming/order, the reorder rename, the ephemeral temp dir, and clips staying out
of any automatic cleanup.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from primocut.config import Settings
from primocut.db import clips as clips_repo
from primocut.db import cuts as cuts_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus
from primocut.render import encode as encode_mod
from primocut.render import section as section_mod
from primocut.worker.render_jobs import render_clips


# ------------------------- primitives -------------------------


def test_section_bounds_margins_and_clamp():
    # Margin added on each side, clamped to [0, duration].
    assert section_mod.section_bounds(100.0, 160.0, 15.0, 600.0) == (85.0, 175.0)
    # Start clamps at 0; end clamps at the duration.
    assert section_mod.section_bounds(5.0, 590.0, 15.0, 600.0) == (0.0, 600.0)
    # Unknown duration (<=0) skips the upper clamp.
    assert section_mod.section_bounds(100.0, 160.0, 15.0, 0.0) == (85.0, 175.0)


def test_build_section_cmd_uses_download_sections_with_margins():
    cmd = section_mod.build_section_cmd("URL", "/tmp/section.%(ext)s", 85.0, 175.0)
    assert "--download-sections" in cmd
    assert cmd[cmd.index("--download-sections") + 1] == "*85.000-175.000"
    # Force keyframes at the cuts: the section spans exactly [dl_start, dl_end]
    # (local time 0 = dl_start, A/V synced) — the plain keyframe copy + merge
    # mangles the timestamps on YouTube's AV1+Opus streams.
    assert "--force-keyframes-at-cuts" in cmd
    assert cmd[-1] == "URL"


def test_build_section_cmd_inserts_extractor_args_before_url():
    cmd = section_mod.build_section_cmd(
        "URL", "/tmp/section.%(ext)s", 85.0, 175.0,
        ["--extractor-args", "youtube:player_client=android"],
    )
    assert "youtube:player_client=android" in cmd
    assert cmd[-1] == "URL"  # extractor args come before the URL


def test_download_section_falls_back_to_next_player_client(tmp_path):
    # The extractor breaks for the first client; the section download must fall
    # back through the configured player_client list, exactly like the audio one.
    import subprocess

    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if "youtube:player_client=android" in cmd:  # extractor broke for android
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: format unavailable")
        (tmp_path / "section.mkv").write_bytes(b"x")  # web succeeds
        return subprocess.CompletedProcess(cmd, 0, "", "")

    out = section_mod.download_section(
        "URL", tmp_path, start_s=100.0, end_s=160.0, margin_s=15.0, duration=600.0,
        settings=Settings(ytdlp_player_clients="android,web", retry_base_delay=0.0),
        runner=fake_run,
    )
    assert out == tmp_path / "section.mkv"
    joined = [" ".join(map(str, c)) for c in cmds]
    assert any("player_client=android" in j for j in joined)  # tried android
    assert any("player_client=web" in j for j in joined)      # fell back to web


def test_download_section_surfaces_ytdlp_stderr_on_failure(tmp_path):
    # Every attempt fails: the raised error must carry yt-dlp's real message, not
    # the opaque "exit status 1" the clip row used to store.
    import subprocess

    import pytest

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Sign in to confirm")

    with pytest.raises(RuntimeError) as ei:
        section_mod.download_section(
            "URL", tmp_path, start_s=100.0, end_s=160.0, margin_s=15.0,
            duration=600.0, settings=Settings(retry_base_delay=0.0), runner=fake_run,
        )
    assert "Sign in to confirm" in str(ei.value)


def test_build_encode_cmd_exact_timestamps_and_codecs():
    st = Settings()
    nv = encode_mod.build_encode_cmd(Path("s.mkv"), Path("o.mp4"),
                                     local_start=95.0, local_dur=60.0,
                                     encoder="nvenc", settings=st)
    # `-ss` before `-i` (frame-exact via the re-encode), `-t` the exact duration.
    assert nv[nv.index("-ss") + 1] == "95.000"
    assert nv.index("-ss") < nv.index("-i")
    assert nv[nv.index("-t") + 1] == "60.000"
    assert "h264_nvenc" in nv and "aac" in nv and "+faststart" in nv

    x264 = encode_mod.build_encode_cmd(Path("s.mkv"), Path("o.mp4"),
                                       local_start=1.0, local_dur=2.0,
                                       encoder="libx264", settings=st)
    assert "libx264" in x264

    copy = encode_mod.build_encode_cmd(Path("s.mkv"), Path("o.mp4"),
                                       local_start=1.0, local_dur=2.0,
                                       encoder="copy", settings=st)
    assert copy[copy.index("-c") + 1] == "copy"


def test_encode_clip_falls_back_to_libx264(tmp_path):
    st = Settings()
    section, out = tmp_path / "s.mkv", tmp_path / "o.mp4"
    section.write_bytes(b"x")
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "h264_nvenc" in cmd:
            raise RuntimeError("nvenc not available")
        out.write_bytes(b"clip")  # libx264 succeeds
        return None

    used, warning = encode_mod.encode_clip(
        section, out, local_start=0.0, local_dur=1.0, profile="nvenc",
        settings=st, runner=runner,
    )
    assert used == "libx264"
    assert warning and "libx264" in warning
    assert [any("h264_nvenc" in c for c in calls), any("libx264" in c for c in calls)] == [True, True]


def test_slug_and_filename():
    assert encode_mod.slugify("Olá, Mundo! (parte 2)") == "ola-mundo-parte-2"
    assert encode_mod.slugify("") == "clip"
    assert encode_mod.clip_filename(0, "T0") == "001-t0.mp4"
    assert encode_mod.clip_filename(11, "T") == "012-t.mp4"


# ------------------------- job (fakes) -------------------------


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed(db, video_id="vid00000001", duration=600.0, n_topics=2):
    from primocut.db import competitors as crepo

    with db.session() as s:
        pid = crepo.create_project(s, "N").id
        repo.upsert_video(s, Video(
            id=video_id, url=f"https://y/{video_id}", title="Live", channel="C",
            duration=duration, status=VideoStatus.DONE, project_id=pid,
        ))
        repo.save_topics(s, video_id, [
            Topic(title=f"T{i}", summary="", start=float(i * 100 + 100),
                  end=float(i * 100 + 160), score=0.5)
            for i in range(n_topics)
        ])
        tids = [t.id for t in repo.get_topic_rows(s, video_id)]
        cut = cuts_repo.ensure_default_cut(s, video_id)
        item_ids = [cuts_repo.add_item(s, cut, duration=duration, topic_id=t).id for t in tids]
        cut_id = cut.id
    return pid, video_id, cut_id, item_ids


class _RecTempDir:
    """A real TemporaryDirectory that records its path, so a test can assert the
    ephemeral section dir is gone after the job."""

    created: list[Path] = []

    def __init__(self, prefix=""):
        self._td = tempfile.TemporaryDirectory(prefix=prefix)
        _RecTempDir.created.append(Path(self._td.name))

    def __enter__(self):
        return self._td.__enter__()

    def __exit__(self, *a):
        return self._td.__exit__(*a)


def _fakes(*, encode_records, fail_dur=None):
    """Fake section download / encode. The download writes a stub section into the
    (ephemeral) temp dir; the encode writes a stub clip to the dest."""

    def download(url, out_dir, *, start_s, end_s, margin_s, duration, settings=None):
        p = Path(out_dir) / "section.mkv"
        p.write_bytes(b"section")
        return p

    def encode(section, dest, *, local_start, local_dur, profile, settings):
        encode_records.append({"local_start": local_start, "local_dur": local_dur,
                               "profile": profile, "dest": Path(dest)})
        if fail_dur is not None and abs(local_dur - fail_dur) < 1e-6:
            raise RuntimeError("boom")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"clip-bytes")
        return ("nvenc" if profile == "nvenc" else profile), None

    return download, encode


def _run(db, tmp_path, video_id, cut_id, records, *, item_ids=None,
         regenerate=False, fail_dur=None):
    dl, enc = _fakes(encode_records=records, fail_dur=fail_dur)
    settings = Settings(clips_dir=tmp_path / "clips")
    return render_clips(
        video_id, cut_id, item_ids, regenerate=regenerate, settings=settings, database=db,
        download_section_fn=dl, encode_clip_fn=enc,
        tempdir_factory=_RecTempDir,
    ), settings


def test_reencode_at_exact_timestamps(tmp_path):
    db = _db(tmp_path)
    _, vid, cid, _ = _seed(db, n_topics=1)  # one topic at 100..160
    records = []
    _RecTempDir.created.clear()
    res, settings = _run(db, tmp_path, vid, cid, records)

    assert res["rendered"] == 1
    # The section spans exactly [dl_start, dl_end] with dl_start = start_s - margin
    # (default margin 15): the item sits at offset `margin` inside it, so
    # local_start = start_s - dl_start = 15; local_dur = 60.
    assert records[0]["local_start"] == 15.0 and records[0]["local_dur"] == 60.0
    # The clip lands under clips_dir/<vid>/<cut>/001-t0.mp4.
    clip_file = settings.clips_dir / vid / str(cid) / "001-t0.mp4"
    assert clip_file.exists()
    # The ephemeral section temp dir is discarded.
    assert all(not d.exists() for d in _RecTempDir.created)


def test_naming_and_order_by_position(tmp_path):
    db = _db(tmp_path)
    _, vid, cid, _ = _seed(db, n_topics=2)
    records = []
    _, settings = _run(db, tmp_path, vid, cid, records)
    root = settings.clips_dir / vid / str(cid)
    assert (root / "001-t0.mp4").exists()
    assert (root / "002-t1.mp4").exists()
    with db.session() as s:
        clips = clips_repo.list_clips_for_cut(s, cid)
        assert [c.filename for c in clips] == ["001-t0.mp4", "002-t1.mp4"]
        assert all(c.state == "done" for c in clips)


def test_cache_by_parameters_skips_second_render(tmp_path):
    db = _db(tmp_path)
    _, vid, cid, _ = _seed(db, n_topics=1)
    records = []
    _run(db, tmp_path, vid, cid, records)
    assert len(records) == 1
    # Same item, same parameters → cache hit, no second encode.
    res2, _ = _run(db, tmp_path, vid, cid, records)
    assert res2["rendered"] == 0 and res2["skipped"] == 1
    assert len(records) == 1  # encode was NOT called again

    # Explicit regeneration forces a re-encode.
    res3, _ = _run(db, tmp_path, vid, cid, records, regenerate=True)
    assert res3["rendered"] == 1
    assert len(records) == 2


def test_reorder_renames_cached_file_without_reencoding(tmp_path):
    db = _db(tmp_path)
    _, vid, cid, item_ids = _seed(db, n_topics=2)
    records = []
    _, settings = _run(db, tmp_path, vid, cid, records)
    assert len(records) == 2
    root = settings.clips_dir / vid / str(cid)
    assert (root / "001-t0.mp4").exists() and (root / "002-t1.mp4").exists()

    # Reverse the order, re-render: parameters are unchanged, so the cache holds
    # and the files are renamed to the new positions (no third/fourth encode).
    with db.session() as s:
        cut = cuts_repo.get_cut(s, vid, cid)
        cuts_repo.reorder_items(s, cut, list(reversed(item_ids)))
    _run(db, tmp_path, vid, cid, records)
    assert len(records) == 2  # nothing re-encoded
    assert (root / "001-t1.mp4").exists() and (root / "002-t0.mp4").exists()
    assert not (root / "001-t0.mp4").exists()  # old name gone (renamed)


def test_per_item_isolation(tmp_path):
    db = _db(tmp_path)
    _, vid, cid, item_ids = _seed(db, n_topics=2)  # durations both 60
    records = []
    # Make the SECOND topic longer so we can fail it by duration.
    with db.session() as s:
        cut = cuts_repo.get_cut(s, vid, cid)
        items = cuts_repo.list_items(s, cid)
        items[1].end_s = items[1].start_s + 90.0  # 90s → the fail trigger
        s.flush()
    res, settings = _run(db, tmp_path, vid, cid, records, fail_dur=90.0)
    assert res["rendered"] == 1 and res["failed"] == 1
    with db.session() as s:
        by_item = clips_repo.clips_by_item(s, cid)
        assert by_item[item_ids[0]].state == "done"
        assert by_item[item_ids[1]].state == "error"
        assert "boom" in by_item[item_ids[1]].error


def test_clips_are_persistent_not_ephemeral(tmp_path):
    """A rendered clip is a deliverable: it lives under clips_dir (persistent),
    NOT inside the throwaway section temp dir, so no automatic temp cleanup can
    reach it. It survives after the job returns."""
    db = _db(tmp_path)
    _, vid, cid, _ = _seed(db, n_topics=1)
    records = []
    _RecTempDir.created.clear()
    _, settings = _run(db, tmp_path, vid, cid, records)
    clip_file = settings.clips_dir / vid / str(cid) / "001-t0.mp4"
    assert clip_file.exists()  # still there after the job
    # It is under clips_dir, not under any ephemeral temp dir.
    assert settings.clips_dir in clip_file.parents
    assert not any(str(clip_file).startswith(str(d)) for d in _RecTempDir.created)


def test_delete_video_removes_clip_files(tmp_path):
    """Explicit live deletion takes the clips with it (the one on-disk artifact);
    there is no automatic sweep, only this explicit path."""
    from primocut.worker.queue import delete_video

    db = _db(tmp_path)
    _, vid, cid, _ = _seed(db, n_topics=1)
    records = []
    _, settings = _run(db, tmp_path, vid, cid, records)
    clip_file = settings.clips_dir / vid / str(cid) / "001-t0.mp4"
    assert clip_file.exists()

    delete_video(vid, settings=settings, database=db, stopper=lambda *a, **k: None)
    assert not (settings.clips_dir / vid).exists()
