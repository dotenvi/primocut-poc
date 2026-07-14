"""Environment diagnostics (`primocut doctor`).

Validates what Phase 0 needs to run end to end: GPU/CUDA visible under WSL2,
FFmpeg, yt-dlp and the Anthropic API key. Each check returns a result with the
fix command when it fails. It installs nothing — it only reports.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess

from pydantic import BaseModel

from .config import Settings, get_settings


class CheckResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    fix: str | None = None


class DiagnosticsReport(BaseModel):
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def check_gpu() -> CheckResult:
    """NVIDIA GPU visible via `nvidia-smi` (driver on Windows, exposed to WSL2)."""
    name = "GPU / CUDA (WSL2)"
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return CheckResult(
            name=name,
            ok=False,
            detail="nvidia-smi not found",
            fix="Install the NVIDIA driver on Windows and enable WSL2 GPU; "
            "confirm with `nvidia-smi`.",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name=name, ok=False, detail="nvidia-smi hung (timeout)")

    if proc.returncode != 0:
        return CheckResult(
            name=name,
            ok=False,
            detail=(proc.stderr or proc.stdout or "returncode != 0").strip(),
            fix="Check the NVIDIA driver and WSL2 GPU support.",
        )
    return CheckResult(name=name, ok=True, detail=proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "ok")


def check_ffmpeg() -> CheckResult:
    name = "FFmpeg"
    path = shutil.which("ffmpeg")
    if path:
        return CheckResult(name=name, ok=True, detail=path)
    return CheckResult(
        name=name,
        ok=False,
        detail="not found on PATH",
        fix="sudo apt install ffmpeg",
    )


def check_ytdlp() -> CheckResult:
    name = "yt-dlp"
    # The pipeline uses `python -m yt_dlp`, so what matters is the module being
    # installed in the current interpreter — not the binary being on PATH.
    if importlib.util.find_spec("yt_dlp") is not None:
        return CheckResult(name=name, ok=True, detail="yt_dlp module installed")
    path = shutil.which("yt-dlp")
    if path:
        return CheckResult(name=name, ok=True, detail=path)
    return CheckResult(
        name=name,
        ok=False,
        detail="yt_dlp module not installed",
        fix="/usr/bin/python3 -m pip --python .venv/bin/python install -U yt-dlp",
    )


def check_api_key(settings: Settings) -> CheckResult:
    """Validate the key of the selected LLM provider (`llm_provider`)."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        env, key = "OPENAI_API_KEY", settings.openai_api_key
    elif provider == "openrouter":
        env, key = "OPENROUTER_API_KEY", settings.openrouter_api_key
    else:
        env, key = "ANTHROPIC_API_KEY", settings.anthropic_api_key

    name = f"{env} ({provider})"
    if key:
        return CheckResult(name=name, ok=True, detail="present")
    return CheckResult(
        name=name,
        ok=False,
        detail="not set",
        fix=f"Copy .env.example to .env and fill in {env} (or export {env}=...).",
    )


def check_youtube_api(settings: Settings) -> CheckResult:
    """Validate the YouTube Data API key with a cheap real call (1 quota unit)."""
    name = "YouTube Data API (PRIMOCUT_YOUTUBE_API_KEY)"
    if not settings.youtube_api_key:
        return CheckResult(
            name=name,
            ok=False,
            detail="not set",
            fix="Set PRIMOCUT_YOUTUBE_API_KEY (YouTube Data API v3 key; "
            "free tier: 10,000 units/day).",
        )
    try:
        from .youtube.client import YouTubeClient

        client = YouTubeClient.from_settings(settings)
        units = client.probe()
    except Exception as exc:  # bad key / quota / network
        return CheckResult(
            name=name,
            ok=False,
            detail=str(exc),
            fix="Check the key and that the YouTube Data API v3 is enabled with "
            "available quota (Google Cloud Console).",
        )
    return CheckResult(name=name, ok=True, detail=f"valid ({units} unit consumed)")


def check_redis(settings: Settings) -> CheckResult:
    """Redis reachable (RQ queue)."""
    name = "Redis"
    try:
        import redis
    except ImportError:
        return CheckResult(
            name=name, ok=False, detail="redis package not installed",
            fix="/usr/bin/python3 -m pip --python .venv/bin/python install redis",
        )
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
    except Exception as exc:  # server down / wrong URL
        return CheckResult(
            name=name, ok=False, detail=str(exc),
            fix="Start Redis: `sudo apt install redis-server` (or `docker run -p 6379:6379 redis`).",
        )
    return CheckResult(name=name, ok=True, detail=settings.redis_url)


def check_db(settings: Settings) -> CheckResult:
    """SQLite database opens and migrates."""
    name = "Database (SQLite)"
    try:
        from .db import Database

        db = Database.from_settings(settings)
        db.init()
    except Exception as exc:
        return CheckResult(
            name=name, ok=False, detail=str(exc),
            fix="Check write permission on data/ (PRIMOCUT_DB_PATH).",
        )
    return CheckResult(name=name, ok=True, detail=str(settings.db_path))


def check_llm_sdk(settings: Settings) -> CheckResult:
    """Confirm the SDK of the selected LLM provider is installed."""
    provider = settings.llm_provider.lower()
    module = "openai" if provider in {"openai", "openrouter"} else "anthropic"
    name = f"SDK {module} ({provider})"
    if importlib.util.find_spec(module) is not None:
        return CheckResult(name=name, ok=True, detail="installed")
    return CheckResult(
        name=name,
        ok=False,
        detail="not installed",
        fix=f"/usr/bin/python3 -m pip --python .venv/bin/python install {module}"
        + ("   (OpenRouter uses the OpenAI SDK)" if provider == "openrouter" else ""),
    )


def run_diagnostics(settings: Settings | None = None) -> DiagnosticsReport:
    settings = settings or get_settings()
    return DiagnosticsReport(
        checks=[
            check_gpu(),
            check_ffmpeg(),
            check_ytdlp(),
            check_api_key(settings),
            check_llm_sdk(settings),
            check_youtube_api(settings),
            check_redis(settings),
            check_db(settings),
        ]
    )
