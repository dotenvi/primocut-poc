"""Tests for the Phase 3 surgical WhisperX boundary refinement.

The alignment engine is fully faked (no whisperx, no torch, no GPU, no network):
the heavy pass runs out-of-process in an isolated venv, so here it is just an
injectable seam. Covers: snapping to word edges, tolerance clamping (never
mid-word), the silent fallback (unavailable / timeout / error → original bounds),
the GPU lock being acquired and released, the extracted window being ONLY the
item's window (never the whole section), and the cache key now folding in the
tolerance. The render-job integration reuses the yt-dlp/FFmpeg fakes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from primocut.config import Settings
from primocut.db import clips as clips_repo
from primocut.db import cuts as cuts_repo
from primocut.db import repo
from primocut.db.session import Database
from primocut.models import Topic, Video, VideoStatus
from primocut.render import align as align_mod
from primocut.worker.render_jobs import render_clips


# ------------------------- snap_bounds (pure) -------------------------


def test_snap_moves_to_nearest_word_edges():
    words = [
        {"text": "a", "start": 99.4, "end": 99.9},
        {"text": "b", "start": 100.1, "end": 100.8},
        {"text": "c", "start": 159.2, "end": 159.8},
        {"text": "d", "start": 160.3, "end": 161.0},
    ]
    # start → nearest word START within tol (100.1, 0.1 away); end → nearest word
    # END within tol (159.8, 0.2 away). Both land on word edges — never mid-word.
    s, e = align_mod.snap_bounds(words, 100.0, 160.0, 3.0)
    assert (round(s, 3), round(e, 3)) == (100.1, 159.8)


def test_snap_keeps_original_when_tolerance_exceeded():
    words = [{"text": "x", "start": 105.0, "end": 106.0}]
    # Nearest start (105) is 5s from 100 and nearest end (106) is 4s from 110 —
    # both beyond ±3s, so each boundary is left untouched.
    s, e = align_mod.snap_bounds(words, 100.0, 110.0, 3.0)
    assert (s, e) == (100.0, 110.0)


def test_snap_empty_words_keeps_original():
    assert align_mod.snap_bounds([], 10.0, 20.0, 3.0) == (10.0, 20.0)


def test_snap_inversion_guard_keeps_original():
    # A snap that would invert the range (start >= end) falls back to the safe
    # originals rather than emitting a negative-length clip.
    words = [{"text": "x", "start": 102.0, "end": 99.0}]
    assert align_mod.snap_bounds(words, 100.0, 101.0, 3.0) == (100.0, 101.0)


# ------------------------- refine_bounds (fakes) -------------------------


class _FakeLock:
    """Records acquire/release so a test can assert the GPU lock was used."""

    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    def __enter__(self):
        self.acquired += 1
        return self

    def __exit__(self, *a):
        self.released += 1
        return False


def _extract(rec):
    def extract(section, out, *, local_start, local_dur):
        rec.append({"local_start": local_start, "local_dur": local_dur})
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"wav")
        return out
    return extract


def _align(words_rel, rec=None):
    def align(wav, *, language, settings, timeout_s):
        if rec is not None:
            rec.append({"language": language, "timeout_s": timeout_s})
        return words_rel
    return align


def test_refine_extracts_only_the_item_window_and_maps_back(tmp_path):
    st = Settings(align_python="X", align_tolerance_s=3.0)
    lock, erec, arec = _FakeLock(), [], []
    # Window-relative words; window_origin = 97 (s0=0). abs start 100.1, abs end 160.5.
    words_rel = [{"text": "b", "start": 3.1, "end": 3.8},
                 {"text": "d", "start": 62.8, "end": 63.5}]
    r = align_mod.refine_bounds(
        tmp_path / "section.mkv", s0=0.0, start_s=100.0, end_s=160.0,
        language="pt", settings=st, workdir=tmp_path, gpu_lock=lock,
        extract_fn=_extract(erec), align_fn=_align(words_rel, arec),
    )
    # The window is ONLY the item ±tol (97 → 163 = 66s), NOT the ±15s section.
    assert erec[0] == {"local_start": 97.0, "local_dur": 66.0}
    assert lock.acquired == 1 and lock.released == 1  # GPU lock used and released
    assert arec[0]["timeout_s"] == st.align_timeout_s
    # Bounds snapped to the mapped word edges (start 100.1, end 160.5).
    assert (round(r[0], 3), round(r[1], 3)) == (100.1, 160.5)


def test_refine_maps_back_with_nonzero_section_origin(tmp_path):
    # The section preserves original timestamps (s0=85); the extract offset is
    # relative to the file, the mapping is relative to the live timeline.
    st = Settings(align_python="X", align_tolerance_s=3.0)
    erec = []
    words_rel = [{"text": "w", "start": 3.2, "end": 3.9}]  # abs start 100.2
    r = align_mod.refine_bounds(
        tmp_path / "s.mkv", s0=85.0, start_s=100.0, end_s=160.0,
        language="pt", settings=st, workdir=tmp_path, gpu_lock=_FakeLock(),
        extract_fn=_extract(erec), align_fn=_align(words_rel),
    )
    assert erec[0]["local_start"] == 12.0  # (97 window origin) - (85 section origin)
    assert round(r[0], 3) == 100.2  # start snapped; end kept (no near word end)


def _fallback_case(tmp_path, align_fn):
    st = Settings(align_python="X", align_tolerance_s=3.0)
    lock = _FakeLock()
    r = align_mod.refine_bounds(
        tmp_path / "s.mkv", s0=0.0, start_s=100.0, end_s=160.0,
        language="pt", settings=st, workdir=tmp_path, gpu_lock=lock,
        extract_fn=_extract([]), align_fn=align_fn,
    )
    return r, lock


def test_refine_unavailable_falls_back_silently(tmp_path):
    def unavailable(wav, **kw):
        raise align_mod.AlignmentUnavailable("no align venv")

    r, lock = _fallback_case(tmp_path, unavailable)
    assert r == (100.0, 160.0)  # original bounds kept
    assert lock.released == 1   # lock released even on the failure path


def test_refine_timeout_falls_back_silently(tmp_path):
    def timeout(wav, **kw):
        raise subprocess.TimeoutExpired(cmd="align", timeout=60)

    r, _ = _fallback_case(tmp_path, timeout)
    assert r == (100.0, 160.0)


def test_refine_error_falls_back_silently(tmp_path):
    def boom(wav, **kw):
        raise RuntimeError("cuda exploded")

    r, _ = _fallback_case(tmp_path, boom)
    assert r == (100.0, 160.0)


def test_run_align_subprocess_unavailable_without_align_python():
    with pytest.raises(align_mod.AlignmentUnavailable):
        align_mod.run_align_subprocess(
            Path("w.wav"), language="pt", settings=Settings(align_python=""),
            timeout_s=1.0,
        )


def test_extract_window_audio_builds_exact_ffmpeg_cmd(tmp_path):
    rec = []

    def runner(cmd, **kw):
        rec.append(cmd)

    align_mod.extract_window_audio(
        tmp_path / "s.mkv", tmp_path / "a.wav",
        local_start=12.0, local_dur=66.0, runner=runner,
    )
    cmd = rec[0]
    assert cmd[cmd.index("-ss") + 1] == "12.000"
    assert cmd.index("-ss") < cmd.index("-i")   # fast seek before input
    assert cmd[cmd.index("-t") + 1] == "66.000"
    assert "-vn" in cmd and "16000" in cmd and "-ac" in cmd  # audio-only 16k mono


# ------------------------- render-job integration -------------------------


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _seed(db, video_id="vid00000001", duration=600.0, n_topics=1):
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
        for t in tids:
            cuts_repo.add_item(s, cut, duration=duration, topic_id=t)
        cut_id = cut.id
    return video_id, cut_id


def _render_fakes(*, encode_records):
    def download(url, out_dir, *, start_s, end_s, margin_s, duration, settings=None):
        p = Path(out_dir) / "section.mkv"
        p.write_bytes(b"section")
        return p

    def encode(section, dest, *, local_start, local_dur, profile, settings):
        encode_records.append({"local_start": local_start, "local_dur": local_dur})
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"clip")
        return ("nvenc" if profile == "nvenc" else profile), None

    return download, encode


def test_render_inserts_aligning_state_and_uses_refined_bounds(tmp_path, monkeypatch):
    db = _db(tmp_path)
    video_id, cut_id = _seed(db)
    records = []
    dl, enc = _render_fakes(encode_records=records)
    settings = Settings(clips_dir=tmp_path / "clips", align_python="X",
                        align_tolerance_s=3.0)
    lock = _FakeLock()

    # A refinement that shifts [100,160] → [101,159]; assert it feeds the encode.
    def fake_refine(section, *, s0, start_s, end_s, language, settings, workdir, gpu_lock):
        with gpu_lock:  # exercise the lock exactly as the real one does
            return 101.0, 159.0

    seen_states = []
    orig_set_state = clips_repo.set_state

    def spy(s, clip_id, state, **f):
        seen_states.append(state)
        return orig_set_state(s, clip_id, state, **f)

    monkeypatch.setattr("primocut.worker.render_jobs.clips_repo.set_state", spy)

    out = render_clips(
        video_id, cut_id, settings=settings, database=db,
        download_section_fn=dl, encode_clip_fn=enc,
        refine_bounds_fn=fake_refine, gpu_lock=lock,
    )
    assert out["rendered"] == 1
    # aligning appears between downloading_section and cutting.
    assert "aligning" in seen_states
    i = seen_states.index
    assert i("downloading_section") < i("aligning") < i("cutting")
    # The refined [101,159] drove the cut, not the original [100,160]; the section
    # origin s0 = dl_start = 100 - 15 (margin), so local_start = 101 - 85 = 16.
    assert records[0] == {"local_start": 16.0, "local_dur": 58.0}
    assert lock.acquired == 1 and lock.released == 1


def test_render_without_align_skips_aligning(tmp_path, monkeypatch):
    db = _db(tmp_path)
    video_id, cut_id = _seed(db)
    records = []
    dl, enc = _render_fakes(encode_records=records)
    settings = Settings(clips_dir=tmp_path / "clips")  # align_python unset

    seen_states = []
    orig = clips_repo.set_state
    monkeypatch.setattr(
        "primocut.worker.render_jobs.clips_repo.set_state",
        lambda s, cid, st, **f: (seen_states.append(st), orig(s, cid, st, **f))[1],
    )
    render_clips(
        video_id, cut_id, settings=settings, database=db,
        download_section_fn=dl, encode_clip_fn=enc,
    )
    assert "aligning" not in seen_states  # no align venv → straight to cutting
    # original bounds [100,160]; s0 = dl_start = 100 - 15 (margin) → local_start = 15.
    assert records[0] == {"local_start": 15.0, "local_dur": 60.0}


def test_cache_key_folds_in_tolerance(tmp_path):
    # Same inputs but a different tolerance yields a different pay-once key.
    a = clips_repo.params_hash(100.0, 160.0, 15.0, "nvenc", 3.0)
    b = clips_repo.params_hash(100.0, 160.0, 15.0, "nvenc", 5.0)
    same = clips_repo.params_hash(100.0, 160.0, 15.0, "nvenc", 3.0)
    assert a != b and a == same
    # Backward-compatible default (no tolerance) differs from an explicit one.
    assert clips_repo.params_hash(100.0, 160.0, 15.0, "nvenc") != a


def test_render_rerenders_when_tolerance_changes(tmp_path):
    db = _db(tmp_path)
    video_id, cut_id = _seed(db)

    def run(tol):
        records = []
        dl, enc = _render_fakes(encode_records=records)
        settings = Settings(clips_dir=tmp_path / "clips", align_python="X",
                            align_tolerance_s=tol)
        return render_clips(
            video_id, cut_id, settings=settings, database=db,
            download_section_fn=dl, encode_clip_fn=enc,
            refine_bounds_fn=lambda *a, **k: (100.0, 160.0), gpu_lock=_FakeLock(),
        )

    assert run(3.0)["rendered"] == 1       # first render
    assert run(3.0)["skipped"] == 1        # same tolerance → cache hit
    assert run(5.0)["rendered"] == 1       # new tolerance → re-render
