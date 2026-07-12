"""Testes dos downloaders (subprocess / chat-downloader mockados)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from primocut.chat import downloader as chat_dl
from primocut.download import audio


# --- extract_video_id (puro) ---


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc123DEF", "abc123DEF"),
        ("https://youtu.be/abc123DEF", "abc123DEF"),
        ("https://www.youtube.com/live/abc123DEF", "abc123DEF"),
        ("https://www.youtube.com/watch?v=abc123DEF&t=90", "abc123DEF"),
    ],
)
def test_extract_video_id(url, expected):
    assert audio.extract_video_id(url) == expected


# --- validate_youtube_url ---


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=SDUQEa7FAms",
        "https://youtu.be/SDUQEa7FAms",
        "https://www.youtube.com/live/SDUQEa7FAms",
        "https://m.youtube.com/watch?v=SDUQEa7FAms&t=90",
        "  https://www.youtube.com/watch?v=SDUQEa7FAms  ",
    ],
)
def test_validate_youtube_url_returns_canonical(url):
    assert audio.validate_youtube_url(url) == "https://www.youtube.com/watch?v=SDUQEa7FAms"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "não é url",
        "ftp://youtube.com/watch?v=SDUQEa7FAms",
        "https://vimeo.com/watch?v=SDUQEa7FAms",  # não é YouTube
        "https://www.youtube.com/watch\\?v\\=SDUQEa7FAms",  # contra-barras de escape
        "https://www.youtube.com/watch?v=curto",  # ID inválido (< 11 chars)
        "https://www.youtube.com/",  # sem vídeo
    ],
)
def test_validate_youtube_url_rejects_bad_input(url):
    with pytest.raises(ValueError):
        audio.validate_youtube_url(url)


# --- fetch_metadata ---


def test_fetch_metadata_parses_ytdlp_json(mocker):
    payload = {
        "id": "abc123DEF",
        "webpage_url": "https://youtube.com/watch?v=abc123DEF",
        "title": "Minha Live",
        "channel": "Canal Y",
        "duration": 3600,
    }
    fake = subprocess.CompletedProcess(["yt-dlp"], 0, json.dumps(payload), "")
    mocker.patch.object(audio.subprocess, "run", return_value=fake)

    video = audio.fetch_metadata("https://youtube.com/watch?v=abc123DEF")
    assert video.id == "abc123DEF"
    assert video.title == "Minha Live"
    assert video.channel == "Canal Y"
    assert video.duration == 3600.0


def test_fetch_metadata_retries_transient(mocker):
    from primocut.config import Settings

    payload = {"id": "abc123DEF", "title": "T", "channel": "C", "duration": 10}
    ok = subprocess.CompletedProcess(["yt-dlp"], 0, json.dumps(payload), "")
    run = mocker.patch.object(
        audio.subprocess, "run", side_effect=[ConnectionError("rede"), ok]
    )
    video = audio.fetch_metadata(
        "https://youtu.be/abc123DEF", Settings(retry_base_delay=0.0)
    )
    assert video.id == "abc123DEF"
    assert run.call_count == 2  # 1 falha transitória + 1 sucesso


def test_download_audio_falls_back_to_next_player_client(mocker, tmp_path):
    from primocut.config import Settings

    cmds = []

    def fake_run(cmd, **kwargs):
        cmds.append(cmd)
        if "youtube:player_client=android" in cmd:
            raise subprocess.CalledProcessError(1, cmd)  # extrator quebrou p/ android
        if "yt_dlp" in cmd:  # simula o download criando o arquivo comprimido
            (tmp_path / "abc123DEF.src.webm").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch.object(audio.subprocess, "run", side_effect=fake_run)
    out = audio.download_audio(
        "https://youtu.be/abc123DEF", tmp_path, video_id="abc123DEF",
        settings=Settings(ytdlp_player_clients="android,web", retry_base_delay=0.0),
    )
    assert out == tmp_path / "abc123DEF.16k.wav"
    joined = [" ".join(map(str, c)) for c in cmds]
    assert any("player_client=android" in j for j in joined)  # tentou android
    assert any("player_client=web" in j for j in joined)      # caiu para web
    assert any(j.startswith("ffmpeg") for j in joined)        # normalizou


def test_download_audio_builds_normalization_command(mocker, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "yt_dlp" in cmd:  # simula o download criando o arquivo comprimido
            (tmp_path / "abc123DEF.src.webm").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    mocker.patch.object(audio.subprocess, "run", side_effect=fake_run)
    out = audio.download_audio(
        "https://youtu.be/abc123DEF", tmp_path, video_id="abc123DEF"
    )

    assert out == tmp_path / "abc123DEF.16k.wav"
    # yt-dlp primeiro (via `python -m yt_dlp`), ffmpeg depois com 16kHz mono
    assert calls[0][:3] == [sys.executable, "-m", "yt_dlp"]
    ffmpeg_cmd = calls[1]
    assert ffmpeg_cmd[0] == "ffmpeg"
    assert "16000" in ffmpeg_cmd
    assert "1" in ffmpeg_cmd  # -ac 1 (mono)


# --- download_chat (opcional) ---


class _FakeDownloader:
    def __init__(self, items):
        self._items = items

    def get_chat(self, url):
        return iter(self._items)


def test_download_chat_parses_messages():
    items = [
        {"time_in_seconds": 5.0, "message": "olá", "author": {"name": "joao"}},
        {"time_in_seconds": 12.0, "message": "kkkk", "author": {"name": "ana"}},
        {"time_in_seconds": None, "message": "superchat sem offset"},  # ignorada
    ]
    msgs = chat_dl.download_chat("url", downloader_factory=lambda: _FakeDownloader(items))
    assert msgs is not None
    assert len(msgs) == 2
    assert msgs[0].offset_seconds == 5.0
    assert msgs[0].author == "joao"
    assert msgs[1].text == "kkkk"


def test_download_chat_returns_none_when_no_replay():
    def boom():
        raise RuntimeError("no chat replay available")

    # fetcher vazio evita cair no yt-dlp real
    assert chat_dl.download_chat("url", downloader_factory=boom, live_chat_fetcher=lambda u: []) is None


def test_download_chat_returns_none_when_empty():
    msgs = chat_dl.download_chat(
        "url", downloader_factory=lambda: _FakeDownloader([]), live_chat_fetcher=lambda u: []
    )
    assert msgs is None


def test_download_chat_falls_back_to_ytdlp_live_chat():
    line = json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": "5000",
                "actions": [
                    {
                        "addChatItemAction": {
                            "item": {
                                "liveChatTextMessageRenderer": {
                                    "message": {"runs": [{"text": "kk"}, {"text": "kk"}]},
                                    "authorName": {"simpleText": "joao"},
                                }
                            }
                        }
                    }
                ],
            }
        }
    )

    def boom():
        raise RuntimeError("chat-downloader indisponível")

    msgs = chat_dl.download_chat(
        "url", downloader_factory=boom, live_chat_fetcher=lambda u: [line, "", "lixo{"]
    )
    assert msgs is not None
    assert len(msgs) == 1
    assert msgs[0].offset_seconds == 5.0
    assert msgs[0].author == "joao"
    assert msgs[0].text == "kkkk"
