"""Tests for competitor comments: automatic collection + manual synthesis.

The YouTube API and the LLM are both faked (no key, no network). Covers the
collection trigger (a video that crossed the outlier threshold — and NOT one that
did not), the paged fetch and its quota accounting, collect-once, the pay-once
synthesis cache and explicit regeneration, graceful degradation when comments are
disabled, the guarantee that the LLM never runs on a schedule (guideline 9), the
golden rule applied to quotes (the model returns indices, the code clamps), and
the project-level aggregation of audience requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from primocut.api.app import create_app, get_db, get_queue_dep, get_settings_dep
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.session import Database
from primocut.metrics import comments as mcomments
from primocut.metrics import enrichment, service
from primocut.worker.enrich_jobs import enrich_videos
from primocut.worker.snapshot_jobs import snapshot_cycle
from primocut.youtube.client import (
    ChannelStats,
    CommentInfo,
    CommentsDisabledError,
    VideoInfo,
    YouTubeError,
)

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

SYNTH_RAW = {
    "themes": [
        {"theme": "A entrevista foi honesta", "mentions": 12, "example_index": 0},
        {"theme": "Querem mais tempo de papo", "mentions": 5, "example_index": 1},
    ],
    "requests": [
        {"type": "guest", "value": "Fulano de Tal", "mentions": 9, "example_index": 1},
        {"type": "topic", "value": "Economia", "mentions": 4, "example_index": 0},
    ],
    "sentiment": "positive",
    "sentiment_note": "The audience loved it.",
}


class FakeLLM:
    """`SelectionLLM` fake: records every call, returns a canned tool payload."""

    def __init__(self, raw: dict | None = None):
        self.calls: list[dict] = []
        self.raw = raw if raw is not None else SYNTH_RAW

    def select(self, *, system, user, tool, tool_name, images=None, model=None) -> dict:
        self.calls.append(
            {"system": system, "user": user, "tool_name": tool_name, "model": model}
        )
        return dict(self.raw)


class FakeYouTube:
    """Duck-typed `YouTubeClient` tracking quota with the real arithmetic."""

    def __init__(self):
        self.channel_stats: dict[str, ChannelStats] = {}
        self.uploads: dict[str, list[str]] = {}
        self.videos: dict[str, VideoInfo] = {}
        self.comments: dict[str, list[CommentInfo]] = {}
        self.disabled: set[str] = set()
        self.comment_calls: list[tuple[str, int]] = []  # (video_id, pages)
        self.units_used = 0

    def get_channels_stats(self, ids):
        self.units_used += 1
        return [self.channel_stats[c] for c in ids if c in self.channel_stats]

    def list_uploads_video_ids(self, playlist_id, *, limit=15):
        self.units_used += 1
        return self.uploads.get(playlist_id, [])[:limit]

    def list_videos(self, video_ids):
        if video_ids:
            self.units_used += (len(video_ids) + 49) // 50
        return [self.videos[v] for v in video_ids if v in self.videos]

    def list_top_comments(self, video_id, *, pages=2):
        self.comment_calls.append((video_id, pages))
        if video_id in self.disabled:
            raise CommentsDisabledError(f"comments are disabled on {video_id}")
        self.units_used += pages
        return list(self.comments.get(video_id, []))


class FakeThumbs:
    def __call__(self, url: str) -> bytes:
        return b"\xff\xd8\xff" + url.encode()


class FakeQueue:
    def __init__(self):
        self.calls: list[tuple] = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return SimpleNamespace(id="rq1")


# ------------------------- fixtures -------------------------


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _settings(tmp_path, **kw) -> Settings:
    return Settings(db_path=tmp_path / "t.db", youtube_api_key="K", **kw)


def _video_info(vid: str, views: int, *, title="A title") -> VideoInfo:
    return VideoInfo(
        video_id=vid,
        title=title,
        published_at=NOW - timedelta(days=30),
        duration_s=600,
        thumbnail_url=f"https://i.ytimg.com/{vid}.jpg",
        view_count=views,
        like_count=10,
        comment_count=5,
    )


def _comment(i: int, likes: int, text: str) -> CommentInfo:
    return CommentInfo(
        comment_id=f"c{i}", text=text, like_count=likes, author=f"user{i}"
    )


def _channel_with_peers(db, yt, cid="UCc", *, target_views: int):
    """A channel whose 12 peers sit at 1000 views (median baseline = 1000), plus
    the scored target `v1`. `target_views / 1000` is therefore its outlier score."""
    with db.session() as s:
        p = crepo.create_project(s, "Niche")
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}")
        crepo.link_channel(s, project_id=p.id, channel_id=cid)
        for i in range(12):
            crepo.upsert_video(
                s, video_id=f"p{i}", channel_id=cid, title=f"Peer {i}",
                published_at=NOW - timedelta(days=40 + i), duration_s=600,
                thumbnail_url=f"https://i.ytimg.com/p{i}.jpg",
            )
        crepo.upsert_video(
            s, video_id="v1", channel_id=cid, title="The outlier",
            published_at=NOW - timedelta(days=30), duration_s=600,
            thumbnail_url="https://i.ytimg.com/v1.jpg",
        )
        project_id = p.id

    yt.channel_stats[cid] = ChannelStats(
        channel_id=cid, subscribers=100, total_views=1000, video_count=13
    )
    yt.uploads[f"UU{cid}"] = []
    for i in range(12):
        yt.videos[f"p{i}"] = _video_info(f"p{i}", 1000, title=f"Peer {i}")
    yt.videos["v1"] = _video_info("v1", target_views, title="The outlier")
    yt.comments["v1"] = [
        _comment(0, 500, "Melhor entrevista do ano, muito honesta"),
        _comment(1, 300, "Traz o Fulano de Tal, por favor"),
    ]
    return project_id


def _cycle(db, tmp_path, yt, **kw):
    return snapshot_cycle(
        "all", settings=_settings(tmp_path, **kw), database=db,
        client=yt, thumbnail_fetcher=FakeThumbs(), now=NOW,
    )


def _stored_comments(db, vid) -> list[tuple[str, int]]:
    with db.session() as s:
        return [(c.comment_id, c.like_count) for c in crepo.list_video_comments(s, vid)]


def _synthesis(db, vid):
    with db.session() as s:
        row = crepo.get_video_enrichment(s, vid, mcomments.KIND)
        if row is None:
            return None
        s.expunge(row)
        return row


# ------------------------- 1. the collection trigger -------------------------


def test_comments_are_collected_only_once_the_video_crosses_the_threshold(tmp_path):
    """A 2x video is not an outlier: no comment call. The same video at 3x is."""
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=2000)  # score 2.0 — below the 3x bracket

    result = _cycle(db, tmp_path, yt)
    assert yt.comment_calls == []
    assert result["comments"] == 0
    assert _stored_comments(db, "v1") == []

    # The video grows to 3x — now it is a definitive outlier and gets collected.
    yt.videos["v1"] = _video_info("v1", 3000, title="The outlier")
    result = _cycle(db, tmp_path, yt)
    assert [vid for vid, _ in yt.comment_calls] == ["v1"]
    assert result["comments"] == 1
    assert _stored_comments(db, "v1") == [("c0", 500), ("c1", 300)]


def test_a_comment_repeated_across_pages_does_not_break_the_collection(tmp_path):
    """Paging is not a snapshot: a shifting relevance order can re-emit a comment."""
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)
    yt.comments["v1"] = [
        _comment(0, 500, "Melhor entrevista do ano"),
        _comment(1, 300, "Traz o Fulano"),
        _comment(0, 500, "Melhor entrevista do ano"),  # page 2 repeated it
    ]

    result = _cycle(db, tmp_path, yt)

    assert result["comments"] == 1
    assert _stored_comments(db, "v1") == [("c0", 500), ("c1", 300)]


def test_outlier_comments_are_collected_only_once(tmp_path):
    """A second cycle over an already-collected outlier issues no call."""
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)

    _cycle(db, tmp_path, yt)
    assert len(yt.comment_calls) == 1

    _cycle(db, tmp_path, yt)
    assert len(yt.comment_calls) == 1  # collect-once


def test_cycle_quota_includes_the_comment_units(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)

    result = _cycle(db, tmp_path, yt, youtube_comment_pages=2)
    assert yt.comment_calls == [("v1", 2)]
    # The cycle reports the shared client's cumulative total, comments included.
    assert result["quota_units"] == yt.units_used
    assert result["quota_units"] >= 2


# ------------------------- 2. the paged fetch -------------------------


def test_list_top_comments_pages_up_to_the_limit_and_stops_early(tmp_path):
    """`pages` caps the fetch; a missing nextPageToken ends it before the cap."""
    from primocut.youtube.client import YouTubeClient

    calls: list[dict] = []

    def transport(endpoint, params):
        calls.append({"endpoint": endpoint, **params})
        page = len(calls)
        body = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": f"c{page}",
                            "snippet": {
                                "textOriginal": f"comment {page}",
                                "likeCount": 10 * page,
                                "authorDisplayName": f"user{page}",
                            },
                        }
                    }
                }
            ]
        }
        if page < 3:  # a third page exists, but `pages=2` must stop at two
            body["nextPageToken"] = f"tok{page}"
        return body

    client = YouTubeClient("K", transport=transport)
    out = client.list_top_comments("v1", pages=2)

    assert [c.comment_id for c in out] == ["c1", "c2"]
    assert [c.like_count for c in out] == [10, 20]
    assert len(calls) == 2
    assert calls[0]["order"] == "relevance"
    assert calls[0]["videoId"] == "v1"
    assert "pageToken" not in calls[0]
    assert calls[1]["pageToken"] == "tok1"  # the token is threaded
    assert client.units_used == 2  # commentThreads.list = 1 unit per page


def test_list_top_comments_stops_when_the_api_runs_out_of_pages(tmp_path):
    from primocut.youtube.client import YouTubeClient

    calls: list[dict] = []

    def transport(endpoint, params):
        calls.append(params)
        return {"items": [], "nextPageToken": None}

    client = YouTubeClient("K", transport=transport)
    assert client.list_top_comments("v1", pages=5) == []
    assert len(calls) == 1  # no nextPageToken → one page only


def test_a_zero_page_limit_is_refused_by_the_settings(tmp_path):
    """Collection is once-per-video, so a zero page limit would silently settle
    every outlier it touched as "collected, no comments" — permanently."""
    import pydantic

    try:
        _settings(tmp_path, youtube_comment_pages=0)
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("a non-positive page limit must be rejected")


def test_comments_disabled_is_raised_as_its_own_error(tmp_path):
    from primocut.youtube.client import YouTubeClient

    def transport(endpoint, params):
        raise YouTubeError(
            'YouTube commentThreads failed: HTTP 403 {"reason": "commentsDisabled"}'
        )

    client = YouTubeClient("K", transport=transport)
    try:
        client.list_top_comments("v1", pages=1)
    except CommentsDisabledError:
        pass
    else:
        raise AssertionError("expected CommentsDisabledError")


# ------------------------- 3. graceful degradation -------------------------


def test_disabled_comments_degrade_without_failing_the_cycle(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)
    yt.disabled.add("v1")

    result = _cycle(db, tmp_path, yt)

    assert result["channels"] == 1  # the cycle completed normally
    assert result["comments"] == 0
    with db.session() as s:
        assert crepo.get_video(s, "v1").comments_state == "disabled"

    # A terminal state: the next cycle must not ask again.
    _cycle(db, tmp_path, yt)
    assert len(yt.comment_calls) == 1


def test_a_comment_fetch_error_is_retried_next_cycle(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)

    boom = FakeYouTube.list_top_comments

    def explode(self, video_id, *, pages=2):
        self.comment_calls.append((video_id, pages))
        raise YouTubeError("transient-ish boom")

    FakeYouTube.list_top_comments = explode
    try:
        result = _cycle(db, tmp_path, yt)
        assert result["channels"] == 1  # never breaks the cycle
        with db.session() as s:
            v = crepo.get_video(s, "v1")
            assert v.comments_state == "error" and v.comments_error
    finally:
        FakeYouTube.list_top_comments = boom

    # "error" is NOT terminal — the next cycle retries.
    _cycle(db, tmp_path, yt)
    assert _stored_comments(db, "v1") == [("c0", 500), ("c1", 300)]


# ------------------------- 4. guideline 9: no automatic LLM -------------------------


def test_snapshot_cycle_collects_comments_but_never_synthesises(tmp_path):
    """The cycle is free + scheduled: it may spend quota, never an LLM call."""
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=10_000)  # a 10x outlier

    _cycle(db, tmp_path, yt)

    assert _stored_comments(db, "v1")  # comments WERE collected (deterministic)
    assert _synthesis(db, "v1") is None  # ...and nothing was ever synthesised


# ------------------------- 5. the synthesis pass -------------------------


def _collected(db, tmp_path, tv=3000):
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=tv)
    _cycle(db, tmp_path, yt)
    return yt


def _synthesise(db, tmp_path, llm, *, regenerate=False, client=None):
    return enrich_videos(
        ["v1"], "comments", regenerate=regenerate, settings=_settings(tmp_path),
        database=db, llm=llm, client=client,
    )


def test_comment_synthesis_records_themes_requests_and_sentiment(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    llm = FakeLLM()

    result = _synthesise(db, tmp_path, llm)

    assert result["enriched"] == 1
    assert len(llm.calls) == 1
    assert llm.calls[0]["tool_name"] == "record_comment_synthesis"
    # The comments are handed to the model INDEXED, most-liked first.
    assert "[0]" in llm.calls[0]["user"] and "Melhor entrevista" in llm.calls[0]["user"]

    row = _synthesis(db, "v1")
    assert row.state == "done"
    payload = row.payload
    assert payload["sentiment"] == "positive"
    assert payload["comments_analysed"] == 2
    assert payload["themes"][0]["theme"] == "A entrevista foi honesta"
    assert payload["requests"][0]["value"] == "Fulano de Tal"
    assert payload["requests"][0]["type"] == "guest"


def test_synthesis_quotes_are_resolved_from_the_stored_comments(tmp_path):
    """The golden rule for quotes: the model returns an index, never text."""
    db = _db(tmp_path)
    _collected(db, tmp_path)

    result = _synthesise(db, tmp_path, FakeLLM())
    assert result["enriched"] == 1

    payload = _synthesis(db, "v1").payload
    # theme[0] pointed at index 0 → the most-liked stored comment, verbatim.
    assert payload["themes"][0]["example"] == {
        "comment_id": "c0",
        "text": "Melhor entrevista do ano, muito honesta",
        "like_count": 500,
        "author": "user0",
    }
    # requests[0] pointed at index 1.
    assert payload["requests"][0]["example"]["comment_id"] == "c1"


def test_an_out_of_range_example_index_is_clamped_to_a_real_comment(tmp_path):
    """A model that invents an index cannot invent a quote — the code clamps."""
    db = _db(tmp_path)
    _collected(db, tmp_path)
    llm = FakeLLM(
        {
            "themes": [{"theme": "T", "mentions": 1, "example_index": 99}],
            "requests": [{"type": "guest", "value": "G", "mentions": 1, "example_index": -5}],
            "sentiment": "mixed",
            "sentiment_note": "n",
        }
    )

    _synthesise(db, tmp_path, llm)

    payload = _synthesis(db, "v1").payload
    assert payload["themes"][0]["example"]["comment_id"] == "c1"  # clamped to the last
    assert payload["requests"][0]["example"]["comment_id"] == "c0"  # clamped to the first


def test_synthesis_is_paid_once_per_comment_set(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    llm = FakeLLM()

    assert _synthesise(db, tmp_path, llm)["enriched"] == 1
    assert len(llm.calls) == 1

    result = _synthesise(db, tmp_path, llm)
    assert result["skipped"] == 1 and result["enriched"] == 0
    assert len(llm.calls) == 1  # unchanged comment set → nothing recharged


def test_explicit_regeneration_refetches_the_comments_and_reruns_the_llm(tmp_path):
    db = _db(tmp_path)
    yt = _collected(db, tmp_path)
    llm = FakeLLM()
    _synthesise(db, tmp_path, llm)
    assert len(llm.calls) == 1
    assert len(yt.comment_calls) == 1

    # New comments arrived since the collection.
    yt.comments["v1"] = [
        _comment(0, 900, "Melhor entrevista do ano, muito honesta"),
        _comment(1, 300, "Traz o Fulano de Tal, por favor"),
        _comment(2, 120, "Quero um episodio sobre economia"),
    ]
    result = _synthesise(db, tmp_path, llm, regenerate=True, client=yt)

    assert len(yt.comment_calls) == 2  # the fetch was redone
    assert len(llm.calls) == 2  # ...and so was the LLM pass
    assert result["enriched"] == 1
    assert _synthesis(db, "v1").payload["comments_analysed"] == 3
    assert len(_stored_comments(db, "v1")) == 3


def test_regenerate_is_rejected_for_the_packaging_kinds(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    try:
        enrich_videos(
            ["v1"], "title", regenerate=True, settings=_settings(tmp_path),
            database=db, llm=FakeLLM(),
        )
    except ValueError as exc:
        assert "regenerate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_synthesis_without_collected_comments_never_calls_the_llm(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=2000)  # not an outlier → no collection
    _cycle(db, tmp_path, yt)
    llm = FakeLLM()

    result = _synthesise(db, tmp_path, llm)

    assert llm.calls == []
    assert result["failed"] == 1
    row = _synthesis(db, "v1")
    assert row.state == "error" and row.error


def test_an_outlier_nobody_commented_on_says_so_instead_of_calling_the_llm(tmp_path):
    """Collected-and-empty is not the same as not-collected — the error says which."""
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)
    yt.comments["v1"] = []
    _cycle(db, tmp_path, yt)

    with db.session() as s:
        assert crepo.get_video(s, "v1").comments_state == "done"  # collection succeeded

    llm = FakeLLM()
    result = _synthesise(db, tmp_path, llm)

    assert llm.calls == []
    assert result["failed"] == 1
    assert "no comments" in _synthesis(db, "v1").error


def test_synthesis_model_override_is_honoured(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    llm = FakeLLM()

    enrich_videos(
        ["v1"], "comments", settings=_settings(tmp_path, enrich_comments_model="cheap-1"),
        database=db, llm=llm,
    )
    assert llm.calls[0]["model"] == "cheap-1"


# ------------------------- 6. the project aggregation -------------------------


def test_audience_requests_merge_across_videos_and_stay_project_scoped(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    project_id = _channel_with_peers(db, yt, target_views=3000)

    # A second project, with its own channel + a synthesis that must NOT leak in.
    with db.session() as s:
        other = crepo.create_project(s, "Other niche")
        crepo.upsert_channel(s, channel_id="UCother", uploads_playlist_id="UUother")
        crepo.link_channel(s, project_id=other.id, channel_id="UCother")
        crepo.upsert_video(
            s, video_id="x1", channel_id="UCother", title="Foreign",
            published_at=NOW - timedelta(days=30),
        )
        crepo.upsert_video_enrichment(
            s, video_id="x1", kind=mcomments.KIND,
            payload={"themes": [], "sentiment": "neutral", "sentiment_note": "",
                     "comments_analysed": 1,
                     "requests": [{"type": "guest", "value": "Ninguem",
                                   "value_key": "ninguem", "mentions": 3, "example": {}}]},
            content_hash="h-x1",
        )

    # Two videos of the project ask for the same guest, spelled differently.
    with db.session() as s:
        for vid, value in (("v1", "Fulano de Tal"), ("p0", "  fulano de tal!  ")):
            crepo.upsert_video_enrichment(
                s, video_id=vid, kind=mcomments.KIND,
                payload={
                    "themes": [], "sentiment": "positive", "sentiment_note": "",
                    "comments_analysed": 2,
                    "requests": [
                        {"type": "guest", "value": value, "value_key": mcomments.request_key(value),
                         "mentions": 5, "example": {}},
                        {"type": "topic", "value": "Economia",
                         "value_key": mcomments.request_key("Economia"),
                         "mentions": 2, "example": {}},
                    ],
                },
                content_hash=f"h-{vid}",
            )

    with db.session() as s:
        data = service.project_dashboard(s, project_id, settings=_settings(tmp_path), now=NOW)

    agg = data["audience_requests"]
    assert agg["videos_synthesised"] == 2
    guests = agg["by_type"]["guest"]
    assert len(guests) == 1
    assert guests[0]["videos"] == 2  # merged despite the spelling difference
    assert guests[0]["mentions"] == 10
    assert sorted(guests[0]["video_ids"]) == ["p0", "v1"]
    assert guests[0]["value"] == "Fulano de Tal"  # the raw spelling, not the key
    assert agg["by_type"]["topic"][0]["value"] == "Economia"
    # The other project's request never leaks in.
    assert all(g["value"] != "Ninguem" for g in guests)


def test_request_key_normalises_spelling_variants():
    assert mcomments.request_key("Fulano de Tal") == mcomments.request_key("  fulano  de tal!  ")
    assert mcomments.request_key("Economia?") == "economia"


# ------------------------- 7. the video report + endpoint -------------------------


def test_video_report_carries_the_comments_and_the_synthesis(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    _synthesise(db, tmp_path, FakeLLM())

    with db.session() as s:
        data = service.video_report(s, "UCc", "v1", settings=_settings(tmp_path), now=NOW)

    assert data["comments"] == {"collected": 2, "state": "done", "error": ""}
    assert data["comment_synthesis"]["state"] == "done"
    assert data["comment_synthesis"]["values"]["sentiment"] == "positive"


def test_video_report_reports_disabled_comments_and_no_synthesis(tmp_path):
    db = _db(tmp_path)
    yt = FakeYouTube()
    _channel_with_peers(db, yt, target_views=3000)
    yt.disabled.add("v1")
    _cycle(db, tmp_path, yt)

    with db.session() as s:
        data = service.video_report(s, "UCc", "v1", settings=_settings(tmp_path), now=NOW)

    assert data["comments"]["state"] == "disabled"
    assert data["comment_synthesis"] is None


def _client(db, queue) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue
    app.dependency_overrides[get_settings_dep] = lambda: Settings(youtube_api_key="K")
    return TestClient(app)


def test_enrich_endpoint_accepts_the_comments_kind_and_regenerate(tmp_path):
    db = _db(tmp_path)
    _collected(db, tmp_path)
    queue = FakeQueue()
    client = _client(db, queue)

    r = client.post("/api/channels/UCc/videos/v1/enrich", json={"kind": "comments"})
    assert r.status_code == 202
    assert queue.calls[0][1] == (["v1"], "comments")
    assert queue.calls[0][2]["regenerate"] is False

    r = client.post(
        "/api/channels/UCc/videos/v1/enrich", json={"kind": "comments", "regenerate": True}
    )
    assert r.status_code == 202
    assert queue.calls[1][2]["regenerate"] is True

    # regenerate has no meaning for a packaging kind.
    r = client.post(
        "/api/channels/UCc/videos/v1/enrich", json={"kind": "title", "regenerate": True}
    )
    assert r.status_code == 400
