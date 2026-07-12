"""Detecção de sinais do chat: densidade por minuto, risadas/emotes e picos.

Puro e determinístico (sem rede/GPU): fácil de testar isoladamente. Picos são
detectados por z-score da densidade de mensagens/minuto (diretriz técnica #4).
"""

from __future__ import annotations

import re
import statistics

from ..models import ChatMessage, ChatSignal, MinuteBucket, Peak

# Risadas em PT-BR: "kkkk", "hahaha", "rsrsrs", "huehue", "aiai"...
_LAUGH_RE = re.compile(r"(k{3,}|(?:ha){2,}|(?:rs){2,}|(?:hue){1,}|(?:ai){2,}|lol)", re.IGNORECASE)

# Emoji unicode (faixas mais comuns) — usados como marcador de reação/emote.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤]"
)


def is_laugh(text: str) -> bool:
    return bool(_LAUGH_RE.search(text or ""))


def count_emotes(text: str) -> int:
    return len(_EMOJI_RE.findall(text or ""))


def compute_chat_signal(
    video_id: str,
    messages: list[ChatMessage],
    *,
    threshold: float = 2.0,
) -> ChatSignal:
    """Agrega mensagens em buckets por minuto e detecta picos por z-score."""
    if not messages:
        return ChatSignal(video_id=video_id, per_minute=[], peaks=[])

    max_minute = max(int(m.offset_seconds // 60) for m in messages)
    buckets = [MinuteBucket(minute=i) for i in range(max_minute + 1)]

    for m in messages:
        minute = int(m.offset_seconds // 60)
        b = buckets[minute]
        b.msgs += 1
        if is_laugh(m.text):
            b.laughs += 1
        b.emotes += count_emotes(m.text)

    peaks = _detect_peaks(buckets, threshold)
    return ChatSignal(video_id=video_id, per_minute=buckets, peaks=peaks)


def _detect_peaks(buckets: list[MinuteBucket], threshold: float) -> list[Peak]:
    counts = [b.msgs for b in buckets]
    if len(counts) < 2:
        return []
    mean = statistics.fmean(counts)
    std = statistics.pstdev(counts)
    if std == 0:
        return []
    peaks: list[Peak] = []
    for b in buckets:
        z = (b.msgs - mean) / std
        if z >= threshold:
            peaks.append(Peak(ts=float(b.minute * 60), intensity=float(b.msgs), z=z))
    return peaks
