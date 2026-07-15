"""Tests for the opt-in AI packaging enrichment (LLM fully faked, no network).

Covers the pay-once cache keyed by content hash (an unchanged hash never calls
the LLM; a new hash does), sharing across projects, per-video failure isolation
in a batch, the batch target selection, the guarantee that NOTHING enriches
without an explicit user action (guideline 9), and the vision plumbing of each
provider.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from primocut.api.app import create_app, get_db, get_queue_dep, get_settings_dep
from primocut.config import Settings
from primocut.db import competitors as crepo
from primocut.db.models import VideoAttributesRow
from primocut.db.session import Database
from primocut.metrics import enrichment
from primocut.worker.enrich_jobs import enrich_videos

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

THUMB_RAW = {
    "face_count": 2,
    "dominant_expression": "shock",
    "has_text": True,
    "text_word_count": 5,
    "text": "OLHA O QUE ELE FEZ",
    "dominant_colors": ["red", "yellow"],
    "contrast": "high",
    "has_arrow": True,
    "has_circle_or_highlight": False,
    "is_confrontation_framing": True,
}

TITLE_RAW = {
    "person_names": ["Fulano", "Ciclano"],
    "topic": "briga no podcast",
    "tone": "drama",
    "quoted_speech": "eu nunca disse isso",
}

RAW_BY_TOOL = {
    "record_thumbnail_attributes": THUMB_RAW,
    "record_title_attributes": TITLE_RAW,
}


class FakeLLM:
    """`SelectionLLM` fake: records every call, optionally raising on chosen ones."""

    def __init__(self, *, fail_calls: set[int] | None = None):
        self.calls: list[dict] = []
        self.fail_calls = fail_calls or set()

    def select(self, *, system, user, tool, tool_name, images=None, model=None) -> dict:
        self.calls.append(
            {"system": system, "user": user, "tool_name": tool_name,
             "images": images, "model": model}
        )
        if len(self.calls) in self.fail_calls:
            raise RuntimeError("llm exploded")
        return dict(RAW_BY_TOOL[tool_name])


class FakeThumbs:
    """Thumbnail byte source keyed by URL (default: deterministic from the URL)."""

    def __init__(self):
        self.content: dict[str, bytes] = {}
        self.fetched: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.fetched.append(url)
        return self.content.get(url, b"\xff\xd8\xff" + url.encode())


class FakeQueue:
    def __init__(self):
        self.calls: list[tuple] = []

    def enqueue(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return SimpleNamespace(id="rq1")


def _db(tmp_path) -> Database:
    db = Database(f"sqlite:///{tmp_path / 't.db'}")
    db.init()
    return db


def _settings(tmp_path, **kw) -> Settings:
    return Settings(db_path=tmp_path / "t.db", youtube_api_key="K", **kw)


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _video(db, cid, vid, *, title="A title", thumb_hash="abc123", days_ago=30, views=1000):
    with db.session() as s:
        crepo.upsert_channel(s, channel_id=cid, uploads_playlist_id=f"UU{cid}")
        crepo.upsert_video(
            s, video_id=vid, channel_id=cid, title=title,
            published_at=NOW - timedelta(days=days_ago),
            thumbnail_url=f"https://i.ytimg.com/{vid}.jpg", thumbnail_hash=thumb_hash,
        )
        crepo.add_video_snapshot(s, video_id=vid, captured_at=NOW, views=views,
                                 likes=0, comments=0)


def _attrs(db, vid, source) -> VideoAttributesRow | None:
    with db.session() as s:
        row = crepo.get_video_attributes(s, vid, source)
        if row is None:
            return None
        s.expunge(row)
        return row


def _run(db, tmp_path, video_ids, kind, *, llm, thumbs=None):
    return enrich_videos(
        video_ids, kind,
        settings=_settings(tmp_path), database=db, llm=llm,
        thumbnail_fetcher=thumbs or FakeThumbs(),
    )


# ------------------------- pay-once cache, keyed by content hash -------------------------


def test_thumbnail_enrichment_pays_once_per_content_hash(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1", thumb_hash="hash-A")
    llm = FakeLLM()

    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)
    assert len(llm.calls) == 1
    row = _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL)
    assert row.content_hash == "hash-A" and row.state == "done"
    assert row.attributes["dominant_expression"] == "shock"      # raw kept
    assert row.attributes["thumb_expression_shock"] is True      # derived facet
    assert row.attributes["thumb_multiple_faces"] is True

    # Same hash → cached: no second LLM call, row untouched.
    result = _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)
    assert len(llm.calls) == 1
    assert result["skipped"] == 1 and result["enriched"] == 0


def test_new_thumbnail_hash_recomputes(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1", thumb_hash="hash-A")
    llm = FakeLLM()
    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)

    # The snapshot cycle detected a new thumbnail (byte hash changed).
    with db.session() as s:
        crepo.update_video_packaging(s, "v1", thumbnail_hash="hash-B")

    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)
    assert len(llm.calls) == 2
    assert _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL).content_hash == "hash-B"


def test_title_enrichment_pays_once_and_recomputes_on_change(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1", title="Original title")
    llm = FakeLLM()

    _run(db, tmp_path, ["v1"], "title", llm=llm)
    assert len(llm.calls) == 1
    assert "Original title" in llm.calls[0]["user"]
    row = _attrs(db, "v1", enrichment.SOURCE_TITLE)
    assert row.attributes["topic"] == "briga no podcast"          # raw kept
    assert row.attributes["title_has_person_name"] is True        # derived facet
    assert row.attributes["title_tone_drama"] is True

    _run(db, tmp_path, ["v1"], "title", llm=llm)
    assert len(llm.calls) == 1  # unchanged title → cached

    with db.session() as s:
        crepo.update_video_packaging(s, "v1", title="A brand new title")
    _run(db, tmp_path, ["v1"], "title", llm=llm)
    assert len(llm.calls) == 2
    assert "A brand new title" in llm.calls[1]["user"]


def test_thumbnail_enrichment_never_fetches_bytes_when_cached(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1", thumb_hash="hash-A")
    llm, thumbs = FakeLLM(), FakeThumbs()

    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm, thumbs=thumbs)
    assert len(thumbs.fetched) == 1
    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm, thumbs=thumbs)
    assert len(thumbs.fetched) == 1  # the gate is checked before any network


def test_empty_thumbnail_hash_never_calls_the_llm(tmp_path):
    """A thumbnail whose bytes could not be hashed at collection time has no usable
    cache key — enriching it would recharge on every click."""
    db = _db(tmp_path)
    _video(db, "UCc", "v1", thumb_hash="")
    llm = FakeLLM()

    result = _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)

    assert llm.calls == []
    assert result["failed"] == 1
    row = _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL)
    assert row.state == "error" and row.error


# ------------------------- shared across projects -------------------------


def test_enrichment_is_shared_across_projects(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCshared", "v1", thumb_hash="hash-A")
    with db.session() as s:
        a = crepo.create_project(s, "A")
        b = crepo.create_project(s, "B")
        crepo.link_channel(s, a.id, "UCshared")
        crepo.link_channel(s, b.id, "UCshared")
    llm = FakeLLM()

    # Enriched once from project A's batch...
    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)
    assert len(llm.calls) == 1

    # ...project B sees it for free (attributes are keyed by the global video id).
    _run(db, tmp_path, ["v1"], "thumbnail", llm=llm)
    assert len(llm.calls) == 1
    assert _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL).attributes["thumb_has_face"] is True


# ------------------------- failure isolation -------------------------


def test_one_failing_video_never_aborts_the_batch(tmp_path):
    db = _db(tmp_path)
    for i in (1, 2, 3):
        _video(db, "UCc", f"v{i}", thumb_hash=f"hash-{i}")
    llm = FakeLLM(fail_calls={2})  # the second video blows up

    result = _run(db, tmp_path, ["v1", "v2", "v3"], "thumbnail", llm=llm)

    assert result["enriched"] == 2 and result["failed"] == 1
    assert _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL).state == "done"
    assert _attrs(db, "v3", enrichment.SOURCE_THUMBNAIL).state == "done"
    failed = _attrs(db, "v2", enrichment.SOURCE_THUMBNAIL)
    assert failed.state == "error" and "llm exploded" in failed.error


def test_failed_video_retries_on_the_next_run(tmp_path):
    db = _db(tmp_path)
    for i in (1, 2):
        _video(db, "UCc", f"v{i}", thumb_hash=f"hash-{i}")
    llm = FakeLLM(fail_calls={2})
    _run(db, tmp_path, ["v1", "v2"], "thumbnail", llm=llm)

    # A second click retries ONLY the failed one (v1 is cached, v2 has no hash).
    llm.fail_calls = set()
    result = _run(db, tmp_path, ["v1", "v2"], "thumbnail", llm=llm)

    assert result == {"kind": "thumbnail", "enriched": 1, "skipped": 1, "failed": 0}
    assert _attrs(db, "v2", enrichment.SOURCE_THUMBNAIL).state == "done"
    assert _attrs(db, "v2", enrichment.SOURCE_THUMBNAIL).error == ""


def test_unknown_kind_is_rejected(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1")
    try:
        _run(db, tmp_path, ["v1"], "comments", llm=FakeLLM())
    except ValueError as exc:
        assert "comments" in str(exc)
    else:
        raise AssertionError("an unknown kind must raise ValueError")


# ------------------------- guideline 9: nothing runs implicitly -------------------------


def test_snapshot_cycle_and_metrics_refresh_never_enrich(tmp_path):
    """The deterministic pipeline must never produce an LLM-sourced attribute row."""
    from primocut.metrics.attributes import refresh_syntactic_attributes
    from primocut.worker.snapshot_jobs import snapshot_cycle
    from primocut.youtube.client import ChannelStats, VideoInfo

    db = _db(tmp_path)
    _video(db, "UCc", "v1", title="Some title", thumb_hash="hash-A")

    class FakeYouTube:
        units_used = 0

        def get_channels_stats(self, ids):
            return [ChannelStats(channel_id=i, subscribers=1, total_views=1, video_count=1)
                    for i in ids]

        def list_uploads_video_ids(self, playlist_id, *, limit=15):
            return []

        def list_videos(self, video_ids):
            return [
                VideoInfo(video_id=v, title="Some title", published_at=NOW, duration_s=600,
                          thumbnail_url=f"https://i.ytimg.com/{v}.jpg",
                          view_count=10, like_count=1, comment_count=1)
                for v in video_ids
            ]

    snapshot_cycle(
        "all", settings=_settings(tmp_path), database=db,
        client=FakeYouTube(), thumbnail_fetcher=FakeThumbs(), now=NOW,
    )
    with db.session() as s:
        refresh_syntactic_attributes(s, ["v1"])

    # The syntactic row exists (deterministic, free)...
    assert _attrs(db, "v1", "syntactic") is not None
    # ...and no LLM-sourced row was ever written.
    assert _attrs(db, "v1", enrichment.SOURCE_TITLE) is None
    assert _attrs(db, "v1", enrichment.SOURCE_THUMBNAIL) is None


# ------------------------- endpoints (opt-in triggers) -------------------------


def _client(db, queue) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_queue_dep] = lambda: queue
    app.dependency_overrides[get_settings_dep] = lambda: Settings(youtube_api_key="K")
    return TestClient(app)


def test_video_enrich_endpoint_enqueues_one_video(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1")
    queue = FakeQueue()

    r = _client(db, queue).post("/api/channels/UCc/videos/v1/enrich", json={"kind": "thumbnail"})

    assert r.status_code == 202
    assert queue.calls[0][1] == (["v1"], "thumbnail")


def test_video_enrich_endpoint_404_and_400(tmp_path):
    db = _db(tmp_path)
    _video(db, "UCc", "v1")
    client = _client(db, FakeQueue())

    assert client.post("/api/channels/UCc/videos/nope/enrich",
                       json={"kind": "title"}).status_code == 404
    assert client.post("/api/channels/UCc/videos/v1/enrich",
                       json={"kind": "comments"}).status_code == 400


def test_project_enrich_endpoint_targets_definitive_outliers_only(tmp_path):
    db = _db(tmp_path)
    # A baseline of 12 videos at 1000 views holds the median at 1000.
    for i in range(12):
        _video(db, "UCc", f"base{i}", views=1000, days_ago=20 + i)
    _video(db, "UCc", "definitive", views=10_000, days_ago=25)   # 10x, mature → target
    _video(db, "UCc", "provisional", views=10_000, days_ago=2)   # 10x but < 7 days → skip
    with db.session() as s:
        proj = crepo.create_project(s, "P")
        crepo.link_channel(s, proj.id, "UCc")
        pid = proj.id
    queue = FakeQueue()

    r = _client(db, queue).post(f"/api/projects/{pid}/enrich", json={"kind": "thumbnail"})

    assert r.status_code == 202
    video_ids, kind = queue.calls[0][1]
    assert set(video_ids) == {"definitive"}
    assert kind == "thumbnail"
    assert r.json()["videos"] == 1


def test_project_enrich_endpoint_404(tmp_path):
    db = _db(tmp_path)
    r = _client(db, FakeQueue()).post("/api/projects/999/enrich", json={"kind": "title"})
    assert r.status_code == 404


# ------------------------- vision plumbing (per provider) -------------------------


def test_anthropic_sends_a_base64_image_block_and_forces_the_tool():
    import base64

    from primocut.analysis.llm import AnthropicLLM

    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(
                type="tool_use", name="record_thumbnail_attributes", input=THUMB_RAW
            )])

    llm = AnthropicLLM(
        Settings(anthropic_api_key="k"), client=SimpleNamespace(messages=FakeMessages())
    )
    out = llm.select(
        system="S", user="Describe it", tool={"name": "record_thumbnail_attributes"},
        tool_name="record_thumbnail_attributes", images=[("image/jpeg", b"JPEGBYTES")],
        model="claude-haiku-4-5",
    )

    assert out == THUMB_RAW
    assert captured["model"] == "claude-haiku-4-5"  # per-call override
    assert captured["tool_choice"] == {"type": "tool", "name": "record_thumbnail_attributes"}
    content = captured["messages"][0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg",
                   "data": base64.standard_b64encode(b"JPEGBYTES").decode()},
    }
    assert content[1] == {"type": "text", "text": "Describe it"}


def test_openai_sends_a_data_url_image_block():
    from primocut.analysis.llm import OpenAILLM

    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=[SimpleNamespace(function=SimpleNamespace(
                    name="record_thumbnail_attributes", arguments='{"face_count": 2}'
                ))]
            ))])

    llm = OpenAILLM(
        Settings(openai_api_key="k"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    out = llm.select(
        system="S", user="Describe it",
        tool={"name": "record_thumbnail_attributes", "input_schema": {"type": "object"}},
        tool_name="record_thumbnail_attributes", images=[("image/png", b"PNGBYTES")],
        model="gpt-4o-mini",
    )

    assert out == {"face_count": 2}
    assert captured["model"] == "gpt-4o-mini"
    content = captured["messages"][1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "Describe it"}


def test_text_only_calls_keep_the_plain_string_content():
    """The topics path must stay byte-identical: no images → no content blocks."""
    from primocut.analysis.llm import AnthropicLLM

    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(
                type="tool_use", name="record_title_attributes", input=TITLE_RAW
            )])

    llm = AnthropicLLM(
        Settings(anthropic_api_key="k"), client=SimpleNamespace(messages=FakeMessages())
    )
    llm.select(system="S", user="A title", tool={"name": "record_title_attributes"},
               tool_name="record_title_attributes")

    assert captured["messages"] == [{"role": "user", "content": "A title"}]
    assert captured["model"] == "claude-sonnet-5"  # no override → provider default
