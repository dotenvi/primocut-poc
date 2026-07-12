"""Download do replay do chat. Sinal OPCIONAL (diretriz técnica #4).

Estratégia com fallback:
1. `chat-downloader` (primário);
2. se falhar/vazio, **fallback yt-dlp `--live-chat`** (parseia o `.live_chat.json`);
3. se ambos falharem, retorna `None` — o pipeline segue sem chat (nunca bloqueia).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..logging_conf import get_logger
from ..models import ChatMessage

logger = get_logger(__name__)


def _default_downloader() -> Any:
    from chat_downloader import ChatDownloader  # import tardio

    return ChatDownloader()


def _via_chat_downloader(
    url: str, factory: Callable[[], Any] | None
) -> list[ChatMessage] | None:
    factory = factory or _default_downloader
    try:
        chat = factory().get_chat(url)
        messages: list[ChatMessage] = []
        for item in chat:
            secs = item.get("time_in_seconds")
            if secs is None:
                continue  # mensagens fora da timeline (ex.: super chats sem offset)
            author = item.get("author") or {}
            messages.append(
                ChatMessage(
                    offset_seconds=float(secs),
                    author=author.get("name", "") if isinstance(author, dict) else str(author),
                    text=item.get("message", "") or "",
                )
            )
    except Exception as exc:  # replay ausente / rede / parsing
        logger.warning("chat-downloader falhou: %s", exc)
        return None
    return messages or None


def _parse_live_chat_line(obj: dict[str, Any]) -> ChatMessage | None:
    action = obj.get("replayChatItemAction") or {}
    offset = action.get("videoOffsetTimeMsec")
    if offset is None:
        return None
    for a in action.get("actions", []):
        item = (a.get("addChatItemAction") or {}).get("item") or {}
        renderer = item.get("liveChatTextMessageRenderer")
        if not renderer:
            continue
        runs = (renderer.get("message") or {}).get("runs") or []
        text = "".join(r.get("text", "") for r in runs)
        author = (renderer.get("authorName") or {}).get("simpleText", "")
        return ChatMessage(offset_seconds=float(offset) / 1000.0, author=author, text=text)
    return None


def _default_live_chat_lines(url: str) -> list[str]:
    import glob
    import os
    import subprocess
    import tempfile

    from ..download.audio import YTDLP

    with tempfile.TemporaryDirectory() as tmp:
        template = os.path.join(tmp, "chat.%(ext)s")
        subprocess.run(
            [*YTDLP, "--skip-download", "--write-subs", "--sub-langs", "live_chat",
             "--no-warnings", "-o", template, url],
            check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        files = glob.glob(os.path.join(tmp, "*.live_chat.json"))
        if not files:
            return []
        with open(files[0], encoding="utf-8") as fh:
            return fh.read().splitlines()


def _via_ytdlp_live_chat(
    url: str, fetcher: Callable[[str], list[str]]
) -> list[ChatMessage] | None:
    try:
        lines = fetcher(url)
    except Exception as exc:
        logger.warning("Fallback yt-dlp --live-chat falhou: %s", exc)
        return None
    messages: list[ChatMessage] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = _parse_live_chat_line(obj)
        if msg is not None:
            messages.append(msg)
    return messages or None


def download_chat(
    url: str,
    *,
    downloader_factory: Callable[[], Any] | None = None,
    live_chat_fetcher: Callable[[str], list[str]] | None = None,
) -> list[ChatMessage] | None:
    """Mensagens do replay (chat-downloader → fallback yt-dlp), ou None."""
    messages = _via_chat_downloader(url, downloader_factory)
    if messages:
        logger.info("Chat: %d mensagens (chat-downloader).", len(messages))
        return messages

    logger.info("Tentando fallback yt-dlp --live-chat…")
    messages = _via_ytdlp_live_chat(url, live_chat_fetcher or _default_live_chat_lines)
    if messages:
        logger.info("Chat: %d mensagens (fallback yt-dlp).", len(messages))
        return messages

    logger.info("Sem replay de chat utilizável.")
    return None
