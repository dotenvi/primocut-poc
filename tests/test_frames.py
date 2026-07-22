"""Deterministic candidate-frame extraction (FFmpeg only — NO LLM).

FFmpeg is faked: the scene pass writes a couple of stub `scene-*.jpg`, the sample
pass writes one stub per call. Covers: the exact commands, scene-then-sample fill
to the requested count, ordered `frame-NNN.jpg` naming, the pay-once skip, and —
the load-bearing property — that extraction takes no LLM at all.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from primocut.render import frames


def _fake_runner(scene_files: int):
    """A subprocess.run fake: the scene command writes `scene_files` stubs; a
    sample command writes its single output; ffprobe returns a duration."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            class P:
                stdout = "120.0"
            return P()
        # scene pass: `-vf select=...`; sample pass: `-frames:v 1` to a named file.
        is_scene = any(isinstance(a, str) and a.startswith("select=") for a in cmd)
        out = cmd[-1]
        if is_scene:
            out_dir = Path(out).parent
            for i in range(scene_files):
                (out_dir / f"scene-{i + 1:03d}.jpg").write_bytes(b"\xff\xd8\xff")
        else:
            Path(out).write_bytes(b"\xff\xd8\xff")

        class R:
            returncode = 0
        return R()

    return runner, calls


def test_no_llm_in_the_module():
    """The extraction module must not import or call any LLM (guideline 9)."""
    # No code path reaches an LLM: strip comments/docstrings and scan for usage.
    src = inspect.getsource(frames)
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    for needle in ("get_llm", ".select(", "import llm", "SelectionLLM", "packaging_prompts"):
        assert needle not in code
    # extract_frames' signature carries no llm/model parameter.
    params = inspect.signature(frames.extract_frames).parameters
    assert not any("llm" in p or "model" in p for p in params)


def test_scene_then_sample_fills_to_count(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "frames"
    runner, calls = _fake_runner(scene_files=1)  # only 1 scene → 2 samples fill

    names = frames.extract_frames(clip, out, count=3, scene_threshold=0.3,
                                  runner=runner, probe_fn=frames.probe_duration)

    assert names == ["frame-001.jpg", "frame-002.jpg", "frame-003.jpg"]
    assert all((out / n).exists() for n in names)
    # No leftover scene stubs remain.
    assert not list(out.glob("scene-*.jpg"))
    # One scene pass + one ffprobe + two sample grabs.
    sample_calls = [c for c in calls if "-frames:v" in c and c[c.index("-frames:v") + 1] == "1"]
    assert len(sample_calls) == 2


def test_enough_scenes_need_no_samples(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "frames"
    runner, calls = _fake_runner(scene_files=5)  # more scenes than requested

    names = frames.extract_frames(clip, out, count=3, scene_threshold=0.3, runner=runner)

    assert names == ["frame-001.jpg", "frame-002.jpg", "frame-003.jpg"]
    assert not any(c[0] == "ffprobe" for c in calls)  # no sampling → no duration probe


def test_pay_once_skips_when_full_set_present(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "frames"
    out.mkdir()
    for i in range(3):
        (out / f"frame-{i + 1:03d}.jpg").write_bytes(b"\xff\xd8\xff")

    runner, calls = _fake_runner(scene_files=2)
    names = frames.extract_frames(clip, out, count=3, scene_threshold=0.3, runner=runner)

    assert names == ["frame-001.jpg", "frame-002.jpg", "frame-003.jpg"]
    assert calls == []  # nothing re-extracted


def test_regenerate_forces_reextraction(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "frames"
    out.mkdir()
    for i in range(3):
        (out / f"frame-{i + 1:03d}.jpg").write_bytes(b"old")

    runner, calls = _fake_runner(scene_files=3)
    frames.extract_frames(clip, out, count=3, scene_threshold=0.3, regenerate=True,
                          runner=runner)
    assert calls  # it ran again


def test_scene_and_sample_commands():
    scene = frames.build_scene_cmd(Path("c.mp4"), "out-%03d.jpg", 0.3)
    assert scene[0] == "ffmpeg"
    assert any(a.startswith("select='gt(scene,0.300)'") for a in scene)
    sample = frames.build_sample_cmd(Path("c.mp4"), Path("f.jpg"), 12.5)
    assert sample[sample.index("-ss") + 1] == "12.500"
    assert sample[sample.index("-frames:v") + 1] == "1"
