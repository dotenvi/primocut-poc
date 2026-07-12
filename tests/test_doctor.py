"""Testes do comando de diagnóstico (doctor), com probes mockados."""

from __future__ import annotations

import subprocess

from primocut import doctor
from primocut.config import Settings


def test_check_ffmpeg_missing(mocker):
    mocker.patch.object(doctor.shutil, "which", return_value=None)
    r = doctor.check_ffmpeg()
    assert r.ok is False
    assert "ffmpeg" in (r.fix or "").lower()


def test_check_ffmpeg_present(mocker):
    mocker.patch.object(doctor.shutil, "which", return_value="/usr/bin/ffmpeg")
    r = doctor.check_ffmpeg()
    assert r.ok is True
    assert r.fix is None


def test_check_ytdlp_missing(mocker):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=None)
    mocker.patch.object(doctor.shutil, "which", return_value=None)
    r = doctor.check_ytdlp()
    assert r.ok is False
    assert "yt-dlp" in (r.fix or "").lower()


def test_check_ytdlp_present_as_module(mocker):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=object())
    r = doctor.check_ytdlp()
    assert r.ok is True


def test_check_gpu_success(mocker):
    fake = subprocess.CompletedProcess(
        args=["nvidia-smi"], returncode=0, stdout="NVIDIA GeForce RTX 3070 Ti", stderr=""
    )
    mocker.patch.object(doctor.subprocess, "run", return_value=fake)
    r = doctor.check_gpu()
    assert r.ok is True
    assert "3070" in r.detail


def test_check_gpu_missing_binary(mocker):
    mocker.patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError())
    r = doctor.check_gpu()
    assert r.ok is False
    assert r.fix is not None


def test_check_gpu_nonzero_returncode(mocker):
    fake = subprocess.CompletedProcess(
        args=["nvidia-smi"], returncode=1, stdout="", stderr="boom"
    )
    mocker.patch.object(doctor.subprocess, "run", return_value=fake)
    r = doctor.check_gpu()
    assert r.ok is False


def test_check_api_key_absent():
    r = doctor.check_api_key(Settings(anthropic_api_key=None))
    assert r.ok is False
    assert "ANTHROPIC_API_KEY" in (r.fix or "")


def test_check_api_key_present():
    r = doctor.check_api_key(Settings(anthropic_api_key="sk-ant-xyz"))
    assert r.ok is True


def test_check_llm_sdk_openrouter_needs_openai(mocker):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=None)
    r = doctor.check_llm_sdk(Settings(llm_provider="openrouter"))
    assert r.ok is False
    assert "openai" in (r.fix or "")


def test_check_llm_sdk_present(mocker):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=object())
    r = doctor.check_llm_sdk(Settings(llm_provider="anthropic"))
    assert r.ok is True


def _fake_redis_ok(mocker):
    import redis

    fake_client = mocker.MagicMock()
    fake_client.ping.return_value = True
    mocker.patch.object(redis.Redis, "from_url", return_value=fake_client)


def test_check_redis_ok(mocker):
    _fake_redis_ok(mocker)
    r = doctor.check_redis(Settings())
    assert r.ok is True


def test_check_redis_down(mocker):
    import redis

    mocker.patch.object(redis.Redis, "from_url", side_effect=ConnectionError("recusou"))
    r = doctor.check_redis(Settings())
    assert r.ok is False
    assert "redis" in (r.fix or "").lower()


def test_check_db_ok(tmp_path):
    r = doctor.check_db(Settings(db_path=tmp_path / "x.db"))
    assert r.ok is True


def test_run_diagnostics_aggregates(mocker, tmp_path):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=None)
    mocker.patch.object(doctor.shutil, "which", return_value=None)
    mocker.patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError())
    import redis

    mocker.patch.object(redis.Redis, "from_url", side_effect=ConnectionError("down"))
    report = doctor.run_diagnostics(
        Settings(anthropic_api_key=None, db_path=tmp_path / "x.db")
    )
    assert len(report.checks) == 7
    assert report.ok is False


def test_run_diagnostics_all_ok(mocker, tmp_path):
    mocker.patch.object(doctor.importlib.util, "find_spec", return_value=object())
    mocker.patch.object(doctor.shutil, "which", return_value="/usr/bin/x")
    fake = subprocess.CompletedProcess(["nvidia-smi"], 0, "RTX 3070 Ti", "")
    mocker.patch.object(doctor.subprocess, "run", return_value=fake)
    _fake_redis_ok(mocker)
    report = doctor.run_diagnostics(
        Settings(anthropic_api_key="sk-ant-xyz", db_path=tmp_path / "x.db")
    )
    assert report.ok is True
