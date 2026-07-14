"""Tests for the YouTube Data API v3 client — fully faked transport (no network).

Covers channel resolution, batched video fetching and, above all, quota
accounting (units consumed per operation).
"""

from __future__ import annotations

import pytest

from primocut.config import Settings
from primocut.youtube.client import (
    YouTubeClient,
    YouTubeError,
    parse_iso8601_duration,
)
from primocut.youtube.resolve import parse_channel_reference


class FakeTransport:
    """Records calls and returns canned JSON per endpoint."""

    def __init__(self, responses: dict):
        self.responses = responses  # endpoint -> list of dicts (one per call) or dict
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, params))
        resp = self.responses[endpoint]
        if isinstance(resp, list):
            return resp.pop(0)
        return resp


def _channels_payload(channel_id="UCabcdefghijklmnopqrstuv", uploads="UUxyz"):
    return {
        "items": [
            {
                "id": channel_id,
                "snippet": {"title": "Cool Channel", "customUrl": "@cool"},
                "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
            }
        ]
    }


def _video_item(vid, seconds="PT3M20S"):
    return {
        "id": vid,
        "snippet": {
            "title": f"Video {vid}",
            "publishedAt": "2026-01-02T03:04:05Z",
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/vi/{vid}/hq.jpg"},
                "maxres": {"url": f"https://i.ytimg.com/vi/{vid}/maxres.jpg"},
            },
        },
        "contentDetails": {"duration": seconds},
    }


# ------------------------- resolution -------------------------


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("UCabcdefghijklmnopqrstuv", {"channel_id": "UCabcdefghijklmnopqrstuv"}),
        ("@handleName", {"handle": "@handleName"}),
        ("https://www.youtube.com/@someChannel", {"handle": "@someChannel"}),
        (
            "https://youtube.com/channel/UCabcdefghijklmnopqrstuv",
            {"channel_id": "UCabcdefghijklmnopqrstuv"},
        ),
        ("https://www.youtube.com/user/LegacyName", {"username": "LegacyName"}),
    ],
)
def test_parse_channel_reference(reference, expected):
    assert parse_channel_reference(reference) == expected


def test_get_channel_by_id_costs_one_unit():
    transport = FakeTransport({"channels": _channels_payload()})
    client = YouTubeClient("KEY", transport=transport)
    info = client.get_channel(channel_id="UCabcdefghijklmnopqrstuv")

    assert info.channel_id == "UCabcdefghijklmnopqrstuv"
    assert info.uploads_playlist_id == "UUxyz"
    assert info.handle == "@cool"
    assert client.units_used == 1
    # The API key is injected into every request.
    assert transport.calls[0][1]["key"] == "KEY"


def test_get_channel_not_found_raises_youtube_error():
    transport = FakeTransport({"channels": {"items": []}})
    client = YouTubeClient("KEY", transport=transport)
    with pytest.raises(YouTubeError):
        client.get_channel(handle="@nobody")


# ------------------------- duration parsing / is_short input -------------------------


@pytest.mark.parametrize(
    "iso,seconds",
    [("PT30S", 30), ("PT1M", 60), ("PT3M20S", 200), ("PT1H2M3S", 3723), ("P0D", 0)],
)
def test_parse_iso8601_duration(iso, seconds):
    assert parse_iso8601_duration(iso) == seconds


# ------------------------- batched video fetch + quota -------------------------


def test_list_videos_batches_50_ids_as_one_unit_each():
    # 60 ids → 2 batches → 2 units.
    ids = [f"vid{i:02d}" for i in range(60)]
    batch1 = {"items": [_video_item(v) for v in ids[:50]]}
    batch2 = {"items": [_video_item(v) for v in ids[50:]]}
    transport = FakeTransport({"videos": [batch1, batch2]})
    client = YouTubeClient("KEY", transport=transport)

    videos = client.list_videos(ids)

    assert len(videos) == 60
    assert client.units_used == 2  # 1 unit per batch of 50, not per id
    # Each request carried at most 50 comma-joined ids.
    assert len(transport.calls[0][1]["id"].split(",")) == 50
    assert len(transport.calls[1][1]["id"].split(",")) == 10


def test_list_uploads_pages_until_limit():
    page1 = {
        "items": [{"contentDetails": {"videoId": f"a{i}"}} for i in range(50)],
        "nextPageToken": "tok",
    }
    page2 = {"items": [{"contentDetails": {"videoId": f"b{i}"}} for i in range(50)]}
    transport = FakeTransport({"playlistItems": [page1, page2]})
    client = YouTubeClient("KEY", transport=transport)

    ids = client.list_uploads_video_ids("UUxyz", limit=70)

    assert len(ids) == 70
    assert client.units_used == 2  # 2 pages


def test_probe_returns_units_consumed():
    transport = FakeTransport({"channels": _channels_payload()})
    client = YouTubeClient("KEY", transport=transport)
    assert client.probe() == 1
    assert client.units_used == 1


def test_from_settings_requires_key():
    with pytest.raises(YouTubeError):
        YouTubeClient.from_settings(Settings(youtube_api_key=None))
    client = YouTubeClient.from_settings(Settings(youtube_api_key="K"))
    assert client._key == "K"
