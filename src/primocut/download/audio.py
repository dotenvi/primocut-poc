"""Download de áudio via yt-dlp e normalização para 16kHz mono com FFmpeg.

`fetch_metadata` e `download_audio` são wrappers finos sobre subprocess, para
serem fáceis de mockar nos testes. `extract_video_id` é puro.
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

# Invoca o yt-dlp pelo interpretador atual (`python -m yt_dlp`), assim funciona
# mesmo quando o binário `.venv/bin/yt-dlp` não está no PATH.
YTDLP = [sys.executable, "-m", "yt_dlp"]

# IDs de vídeo do YouTube têm exatamente 11 caracteres deste alfabeto.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def extract_video_id(url: str) -> str:
    """Extrai o ID do vídeo de uma URL do YouTube (watch?v=, youtu.be, /live/)."""
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.lstrip("/").split("/")[0]
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    # formatos /live/<id> ou /embed/<id>
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[0] in {"live", "embed", "shorts"} and len(parts) > 1:
        return parts[1]
    if parts:
        return parts[-1]
    raise ValueError(f"Não consegui extrair o ID do vídeo de: {url}")


def validate_youtube_url(url: str) -> str:
    """Valida o formato do link e retorna a URL canônica do vídeo.

    Aceita links de vídeo do YouTube (watch?v=, youtu.be, /live/, /shorts/) e
    devolve `https://www.youtube.com/watch?v=<ID>`. Levanta ValueError com uma
    mensagem clara quando o link não é um vídeo do YouTube reconhecível — o que
    também rejeita URLs mal formadas (ex.: com contra-barras de escape do shell).
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL vazia.")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"A URL precisa começar com http:// ou https:// — recebido: {url!r}")

    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        raise ValueError(
            f"Não é um link do YouTube: {url!r}. "
            "Use algo como https://www.youtube.com/watch?v=VIDEO_ID"
        )

    try:
        video_id = extract_video_id(raw)
    except ValueError:
        video_id = ""

    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError(
            f"Não encontrei um ID de vídeo válido (11 caracteres) em: {url!r}. "
            "Confira se o link é de um vídeo (formato https://www.youtube.com/watch?v=VIDEO_ID) "
            "e se não há caracteres de escape (contra-barras) colados por engano."
        )

    return f"https://www.youtube.com/watch?v={video_id}"


def _retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        attempts=settings.retry_attempts,
        base_delay=settings.retry_base_delay,
        max_delay=settings.retry_max_delay,
    )


def fetch_metadata(url: str, settings: Settings | None = None) -> Video:
    """Coleta metadados via `yt-dlp -J` (sem baixar mídia), com retry transitório."""
    settings = settings or get_settings()

    def _run() -> dict:
        proc = subprocess.run(
            [*YTDLP, "-J", "--no-warnings", url],
            capture_output=True, text=True, check=True,
        )
        return json.loads(proc.stdout)

    data = retry(_run, policy=_retry_policy(settings), is_retryable=is_transient, label="metadados")
    return Video(
        id=data.get("id") or extract_video_id(url),
        url=data.get("webpage_url", url),
        title=data.get("title", "(sem título)"),
        channel=data.get("channel") or data.get("uploader", "(desconhecido)"),
        duration=float(data.get("duration") or 0.0),
        status=VideoStatus.DOWNLOADING,
    )


def download_audio(
    url: str, out_dir: Path, *, video_id: str | None = None, settings: Settings | None = None
) -> Path:
    """Baixa o áudio e normaliza para WAV 16kHz mono. Retorna o caminho do .wav.

    Resiliência: retry transitório (rede) + fallback de `player_client` do yt-dlp
    (o extrator do YouTube quebra com frequência).
    """
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    vid = video_id or extract_video_id(url)
    raw_template = str(out_dir / f"{vid}.%(ext)s")
    wav_path = out_dir / f"{vid}.wav"
    normalized = out_dir / f"{vid}.16k.wav"

    # Retomada: se o WAV normalizado já existe, reaproveita (evita rebaixar).
    if normalized.exists() and normalized.stat().st_size > 0:
        logger.info("Reaproveitando áudio já baixado: %s", normalized)
        return normalized

    clients = [c.strip() for c in settings.ytdlp_player_clients.split(",") if c.strip()]
    attempts = [["--extractor-args", f"youtube:player_client={c}"] for c in clients] or [[]]
    policy = _retry_policy(settings)

    # 1. yt-dlp extrai o melhor áudio para WAV — tenta cada player_client;
    #    dentro de cada um, retry para erros transitórios de rede.
    last_exc: Exception | None = None
    for extra in attempts:
        try:
            retry(
                lambda e=extra: subprocess.run(
                    [*YTDLP, "-x", "--audio-format", "wav", "--no-warnings", *e,
                     "-o", raw_template, url],
                    check=True,
                ),
                policy=policy, is_retryable=is_transient, label="download áudio",
            )
            break
        except Exception as exc:  # yt-dlp quebrou p/ este client → tenta o próximo
            last_exc = exc
            logger.warning("Download falhou (extractor-args=%s): %s", extra or "padrão", exc)
    else:
        raise RuntimeError(
            f"Falha ao baixar o áudio após {len(attempts)} configuração(ões) de yt-dlp. "
            "Tente atualizar o yt-dlp ou ajustar PRIMOCUT_YTDLP_PLAYER_CLIENTS. "
            f"Último erro: {last_exc}"
        )

    # 2. FFmpeg normaliza para 16kHz mono (entrada do Whisper).
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-ar", "16000", "-ac", "1", str(normalized)],
        check=True,
    )
    logger.info("Áudio normalizado: %s", normalized)
    return normalized
