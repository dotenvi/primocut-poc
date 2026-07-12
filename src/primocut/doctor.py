"""Diagnóstico do ambiente (`primocut doctor`).

Valida o que a Fase 0 precisa para rodar de ponta a ponta:
GPU/CUDA visível no WSL2, FFmpeg, yt-dlp e a chave da API Anthropic.
Cada check retorna um resultado com o comando de correção quando falha.
Não instala nada — apenas reporta.
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
    """GPU NVIDIA visível via `nvidia-smi` (driver no Windows, exposto ao WSL2)."""
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
            detail="nvidia-smi não encontrado",
            fix="Instale o driver NVIDIA no Windows e habilite o WSL2 GPU; "
            "confirme com `nvidia-smi`.",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name=name, ok=False, detail="nvidia-smi travou (timeout)")

    if proc.returncode != 0:
        return CheckResult(
            name=name,
            ok=False,
            detail=(proc.stderr or proc.stdout or "returncode != 0").strip(),
            fix="Verifique o driver NVIDIA e o suporte a GPU no WSL2.",
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
        detail="não encontrado no PATH",
        fix="sudo apt install ffmpeg",
    )


def check_ytdlp() -> CheckResult:
    name = "yt-dlp"
    # O pipeline usa `python -m yt_dlp`, então o que importa é o módulo estar
    # instalado no interpretador atual — não o binário estar no PATH.
    if importlib.util.find_spec("yt_dlp") is not None:
        return CheckResult(name=name, ok=True, detail="módulo yt_dlp instalado")
    path = shutil.which("yt-dlp")
    if path:
        return CheckResult(name=name, ok=True, detail=path)
    return CheckResult(
        name=name,
        ok=False,
        detail="módulo yt_dlp não instalado",
        fix="/usr/bin/python3 -m pip --python .venv/bin/python install -U yt-dlp",
    )


def check_api_key(settings: Settings) -> CheckResult:
    """Valida a chave do provedor de LLM selecionado (`llm_provider`)."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        env, key = "OPENAI_API_KEY", settings.openai_api_key
    elif provider == "openrouter":
        env, key = "OPENROUTER_API_KEY", settings.openrouter_api_key
    else:
        env, key = "ANTHROPIC_API_KEY", settings.anthropic_api_key

    name = f"{env} ({provider})"
    if key:
        return CheckResult(name=name, ok=True, detail="presente")
    return CheckResult(
        name=name,
        ok=False,
        detail="não setada",
        fix=f"Copie .env.example para .env e preencha {env} (ou export {env}=...).",
    )


def check_redis(settings: Settings) -> CheckResult:
    """Redis acessível (fila RQ)."""
    name = "Redis"
    try:
        import redis
    except ImportError:
        return CheckResult(
            name=name, ok=False, detail="pacote redis não instalado",
            fix="/usr/bin/python3 -m pip --python .venv/bin/python install redis",
        )
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
    except Exception as exc:  # servidor fora do ar / URL errada
        return CheckResult(
            name=name, ok=False, detail=str(exc),
            fix="Suba o Redis: `sudo apt install redis-server` (ou `docker run -p 6379:6379 redis`).",
        )
    return CheckResult(name=name, ok=True, detail=settings.redis_url)


def check_db(settings: Settings) -> CheckResult:
    """Banco SQLite abre e migra."""
    name = "Banco (SQLite)"
    try:
        from .db import Database

        db = Database.from_settings(settings)
        db.init()
    except Exception as exc:
        return CheckResult(
            name=name, ok=False, detail=str(exc),
            fix="Verifique permissão de escrita em data/ (PRIMOCUT_DB_PATH).",
        )
    return CheckResult(name=name, ok=True, detail=str(settings.db_path))


def check_llm_sdk(settings: Settings) -> CheckResult:
    """Confirma que o SDK do provedor de LLM selecionado está instalado."""
    provider = settings.llm_provider.lower()
    module = "openai" if provider in {"openai", "openrouter"} else "anthropic"
    name = f"SDK {module} ({provider})"
    if importlib.util.find_spec(module) is not None:
        return CheckResult(name=name, ok=True, detail="instalado")
    return CheckResult(
        name=name,
        ok=False,
        detail="não instalado",
        fix=f"/usr/bin/python3 -m pip --python .venv/bin/python install {module}"
        + ("   (OpenRouter usa o SDK da OpenAI)" if provider == "openrouter" else ""),
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
            check_redis(settings),
            check_db(settings),
        ]
    )
