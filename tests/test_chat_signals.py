"""Testes da detecção de sinais do chat (densidade, risadas, picos por z-score)."""

from __future__ import annotations

from primocut.chat.signals import compute_chat_signal, count_emotes, is_laugh
from primocut.models import ChatMessage


def test_is_laugh_variants():
    assert is_laugh("kkkkk")
    assert is_laugh("KKKK isso")
    assert is_laugh("hahaha")
    assert is_laugh("aiaiai rsrsrs")
    assert not is_laugh("ok")
    assert not is_laugh("falando sério agora")


def test_count_emotes_counts_emoji():
    assert count_emotes("boa 😂😂") == 2
    assert count_emotes("sem emote aqui") == 0


def test_buckets_by_minute():
    msgs = [
        ChatMessage(offset_seconds=10, text="a"),
        ChatMessage(offset_seconds=20, text="b"),
        ChatMessage(offset_seconds=70, text="c"),
    ]
    sig = compute_chat_signal("vid", msgs)
    assert sig.per_minute[0].minute == 0
    assert sig.per_minute[0].msgs == 2
    assert sig.per_minute[1].msgs == 1


def test_laughs_and_emotes_aggregated_per_minute():
    msgs = [
        ChatMessage(offset_seconds=5, text="kkkk"),
        ChatMessage(offset_seconds=6, text="sério 😂"),
        ChatMessage(offset_seconds=7, text="normal"),
    ]
    sig = compute_chat_signal("vid", msgs)
    assert sig.per_minute[0].msgs == 3
    assert sig.per_minute[0].laughs == 1
    assert sig.per_minute[0].emotes == 1


def test_peak_detected_on_spike():
    msgs = []
    # 10 minutos com 2 msgs cada
    for minute in range(10):
        for _ in range(2):
            msgs.append(ChatMessage(offset_seconds=minute * 60 + 1, text="oi"))
    # minuto 10 com um pico de 50 msgs
    for _ in range(50):
        msgs.append(ChatMessage(offset_seconds=10 * 60 + 1, text="kkkk"))

    sig = compute_chat_signal("vid", msgs, threshold=2.0)
    assert len(sig.peaks) == 1
    peak = sig.peaks[0]
    assert peak.ts == 10 * 60
    assert peak.intensity == 50
    assert peak.z >= 2.0


def test_no_peaks_when_flat():
    msgs = [ChatMessage(offset_seconds=m * 60 + 1, text="oi") for m in range(10)]
    sig = compute_chat_signal("vid", msgs, threshold=2.0)
    assert sig.peaks == []


def test_empty_messages_returns_empty_signal():
    sig = compute_chat_signal("vid", [])
    assert sig.per_minute == []
    assert sig.peaks == []
