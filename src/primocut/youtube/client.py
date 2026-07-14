"""YouTube Data API v3 client — public data only, with quota accounting.

Quota accounting: every call adds its cost (in units) to `units_used` and logs
the per-operation and cumulative totals. Batching is exploited where the API
allows it — `videos.list` with up to 50 IDs costs a single unit.

The HTTP transport is injectable (`transport(endpoint, params) -> dict`) so the
whole client is testable without network access.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..config import Settings
from ..logging_conf import get_logger
from ..resilience import RetryPolicy, is_transient, retry

logger = get_logger(__name__)

_API_ROOT = "https://www.googleapis.com/youtube/v3"

# Quota cost per API operation (YouTube Data API v3). videos.list is 1 unit
# regardless of how many IDs (up to 50) are requested.
QUOTA_COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "search.list": 100,
}

# A well-known, stable channel used for the doctor's cheap liveness probe.
_PROBE_CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"  # Google Developers

Transport = Callable[[str, dict], dict]


class YouTubeError(RuntimeError):
    """A permanent YouTube API error (bad key, not found, quota exceeded). It is
    non-transient: callers degrade and log, never crash."""


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    handle: str
    uploads_playlist_id: str


@dataclass
class VideoInfo:
    video_id: str
    title: str
    published_at: datetime | None
    duration_s: int
    thumbnail_url: str
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0


@dataclass
class ChannelStats:
    channel_id: str
    subscribers: int
    total_views: int
    video_count: int


def _urllib_transport(endpoint: str, params: dict) -> dict:
    url = f"{_API_ROOT}/{endpoint}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code == 429 or exc.code >= 500:
            # Transient: let resilience.retry back off and try again.
            raise ConnectionError(
                f"YouTube {endpoint} transient HTTP {exc.code}"
            ) from exc
        raise YouTubeError(
            f"YouTube {endpoint} failed: HTTP {exc.code} {body[:300]}"
        ) from exc
    # urllib.error.URLError (network) propagates and is classified transient.


class YouTubeClient:
    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._key = api_key
        self._transport = transport or _urllib_transport
        self.units_used = 0
        self._policy = (
            RetryPolicy(
                attempts=settings.retry_attempts,
                base_delay=settings.retry_base_delay,
                max_delay=settings.retry_max_delay,
            )
            if settings is not None
            else RetryPolicy()
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "YouTubeClient":
        if not settings.youtube_api_key:
            raise YouTubeError("PRIMOCUT_YOUTUBE_API_KEY is not set.")
        return cls(settings.youtube_api_key, settings=settings)

    # -- core: one accounted, retried request --

    def _call(self, endpoint: str, params: dict, *, cost: int) -> dict:
        result = retry(
            lambda: self._transport(endpoint, {**params, "key": self._key}),
            policy=self._policy,
            is_retryable=is_transient,
            label=f"youtube.{endpoint}",
        )
        self.units_used += cost
        logger.info(
            "youtube %s: +%d unit(s) (cumulative=%d)", endpoint, cost, self.units_used
        )
        return result

    # -- operations --

    def get_channel(
        self,
        *,
        channel_id: str | None = None,
        handle: str | None = None,
        username: str | None = None,
    ) -> ChannelInfo:
        """Resolve a channel to its id + uploads playlist (1 unit)."""
        params: dict = {"part": "snippet,contentDetails"}
        if channel_id:
            params["id"] = channel_id
        elif handle:
            params["forHandle"] = handle
        elif username:
            params["forUsername"] = username
        else:
            raise ValueError("get_channel needs channel_id, handle or username.")

        data = self._call("channels", params, cost=QUOTA_COSTS["channels.list"])
        items = data.get("items") or []
        if not items:
            raise YouTubeError(
                f"Channel not found ({channel_id or handle or username!r})."
            )
        item = items[0]
        snippet = item.get("snippet", {})
        uploads = (
            item.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads", "")
        )
        return ChannelInfo(
            channel_id=item["id"],
            title=snippet.get("title", ""),
            handle=snippet.get("customUrl", ""),
            uploads_playlist_id=uploads,
        )

    def list_uploads_video_ids(self, playlist_id: str, *, limit: int = 120) -> list[str]:
        """Page through the uploads playlist (playlistItems.list, 1 unit/page of 50)."""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < limit:
            params: dict = {
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(50, limit - len(ids)),
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._call(
                "playlistItems", params, cost=QUOTA_COSTS["playlistItems.list"]
            )
            for item in data.get("items", []):
                vid = item.get("contentDetails", {}).get("videoId")
                if vid:
                    ids.append(vid)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return ids[:limit]

    def list_videos(self, video_ids: list[str]) -> list[VideoInfo]:
        """Fetch metadata in batches of 50 (videos.list, 1 unit per batch)."""
        out: list[VideoInfo] = []
        for start in range(0, len(video_ids), 50):
            chunk = video_ids[start : start + 50]
            data = self._call(
                "videos",
                {
                    "part": "snippet,contentDetails,statistics",
                    "id": ",".join(chunk),
                    "maxResults": 50,
                },
                cost=QUOTA_COSTS["videos.list"],
            )
            for item in data.get("items", []):
                out.append(_parse_video(item))
        return out

    def get_channels_stats(self, channel_ids: list[str]) -> list[ChannelStats]:
        """Fetch channel statistics in batches of 50 (channels.list, 1 unit/batch)."""
        out: list[ChannelStats] = []
        for start in range(0, len(channel_ids), 50):
            chunk = channel_ids[start : start + 50]
            data = self._call(
                "channels",
                {"part": "statistics", "id": ",".join(chunk), "maxResults": 50},
                cost=QUOTA_COSTS["channels.list"],
            )
            for item in data.get("items", []):
                stats = item.get("statistics", {})
                out.append(
                    ChannelStats(
                        channel_id=item["id"],
                        subscribers=_int(stats.get("subscriberCount")),
                        total_views=_int(stats.get("viewCount")),
                        video_count=_int(stats.get("videoCount")),
                    )
                )
        return out

    def probe(self) -> int:
        """Cheap liveness/quota check for the doctor. Returns the units consumed."""
        before = self.units_used
        self._call(
            "channels",
            {"part": "id", "id": _PROBE_CHANNEL_ID},
            cost=QUOTA_COSTS["channels.list"],
        )
        return self.units_used - before


# ------------------------- parsing helpers -------------------------

_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def parse_iso8601_duration(value: str) -> int:
    """Convert an ISO-8601 duration (e.g. "PT1H2M3S") to whole seconds."""
    if not value:
        return 0
    m = _ISO_DURATION_RE.match(value)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _parse_published_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _best_thumbnail_url(thumbnails: dict) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbnails.get(key)
        if entry and entry.get("url"):
            return entry["url"]
    return ""


def _int(value) -> int:
    """Parse a stringy count from the API; missing/hidden counts become 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_video(item: dict) -> VideoInfo:
    snippet = item.get("snippet", {})
    content = item.get("contentDetails", {})
    stats = item.get("statistics", {})
    return VideoInfo(
        video_id=item["id"],
        title=snippet.get("title", ""),
        published_at=_parse_published_at(snippet.get("publishedAt", "")),
        duration_s=parse_iso8601_duration(content.get("duration", "")),
        thumbnail_url=_best_thumbnail_url(snippet.get("thumbnails", {})),
        view_count=_int(stats.get("viewCount")),
        like_count=_int(stats.get("likeCount")),
        comment_count=_int(stats.get("commentCount")),
    )
