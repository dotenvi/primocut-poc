"""LLM analysis tests.

Central focus: prove that timestamps are ALWAYS derived from the transcript
segments (index selection), never invented by the LLM. Everything with a fake
LLM — no network.
"""

from __future__ import annotations

from types import SimpleNamespace

import json

from primocut.analysis.consolidate import merge_overlapping
import pytest

from primocut.analysis.llm import (
    AnthropicLLM,
    OpenAILLM,
    OpenRouterLLM,
    ToolCallError,
    get_llm,
)
from primocut.config import Settings
from primocut.analysis.topics import extract_topics
from primocut.models import Segment, Topic, Transcript


def _transcript(n: int = 6, seg_len: float = 10.0) -> Transcript:
    return Transcript(
        video_id="vid",
        language="pt",
        model="m",
        segments=[
            Segment(index=i, start=i * seg_len, end=(i + 1) * seg_len, text=f"fala {i}")
            for i in range(n)
        ],
    )


class FakeLLM:
    """Returns canonical payloads by tool name."""

    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.calls: list[str] = []

    def select(self, *, system, user, tool, tool_name):
        self.calls.append(tool_name)
        return self.payloads.get(tool_name, {})


def test_extract_topics_resolves_indices_to_seconds():
    t = _transcript()
    llm = FakeLLM(
        {
            "record_topics": {
                "topics": [
                    {"title": "A", "summary": "s", "start_index": 0, "end_index": 2, "score": 0.9},
                    {"title": "B", "summary": "s", "start_index": 3, "end_index": 5, "score": 0.6},
                ]
            }
        }
    )
    topics = extract_topics(t, llm)
    assert [tp.title for tp in topics] == ["A", "B"]
    # timestamps come from the segments, not from the LLM
    assert topics[0].start == t.segments[0].start == 0.0
    assert topics[0].end == t.segments[2].end == 30.0
    assert topics[1].start == 30.0
    assert topics[1].end == 60.0


def test_extract_topics_clamps_invented_out_of_range_indices():
    t = _transcript()
    llm = FakeLLM(
        {
            "record_topics": {
                "topics": [
                    {"title": "X", "summary": "s", "start_index": -3, "end_index": 999, "score": 0.5}
                ]
            }
        }
    )
    topics = extract_topics(t, llm)
    # absurd indices are clamped to the transcript's real limits
    assert topics[0].start == 0.0
    assert topics[0].end == t.duration == 60.0


def test_extract_topics_skips_malformed_items_without_crashing():
    from primocut.analysis.parse import coerce_items

    t = _transcript()
    llm = FakeLLM(
        {
            "record_topics": {
                "topics": [
                    {"title": "bom", "summary": "s", "start_index": 0, "end_index": 1, "score": 0.7},
                    "this is not an object",  # invalid item (string)
                    {"title": "no indices", "summary": "s", "score": 0.5},  # missing indices
                ]
            }
        }
    )
    topics = extract_topics(t, llm)
    # only the valid item survives; the bad ones are ignored (does not crash the analysis)
    assert [tp.title for tp in topics] == ["bom"]

    # coerce_items handles an indexed dict {"0": {...}} instead of a list
    items = coerce_items({"topics": {"0": {"a": 1}, "1": {"b": 2}}}, "topics")
    assert items == [{"a": 1}, {"b": 2}]


class _Transient(Exception):
    status_code = 503


def test_extract_topics_retries_transient_then_succeeds():
    t = _transcript()  # 1 window
    payload = {"topics": [{"title": "A", "summary": "s", "start_index": 0, "end_index": 1, "score": 0.7}]}

    class FlakyLLM:
        def __init__(self):
            self.n = 0

        def select(self, **kwargs):
            self.n += 1
            if self.n < 3:
                raise _Transient()
            return payload

    llm = FlakyLLM()
    topics = extract_topics(t, llm, Settings(retry_attempts=5, retry_base_delay=0.0))
    assert [x.title for x in topics] == ["A"]
    assert llm.n == 3  # 2 transient failures + 1 success


def test_extract_topics_stops_on_permanent_keeps_partial_and_warns():
    t = _transcript(30)  # with a small window => 3 windows
    good = {"topics": [{"title": "W1", "summary": "s", "start_index": 0, "end_index": 1, "score": 0.7}]}

    class HalfLLM:
        def __init__(self):
            self.n = 0

        def select(self, **kwargs):
            self.n += 1
            if self.n == 1:
                return good
            raise RuntimeError("out of credit")  # permanent (no status_code)

    warns: list[str] = []
    settings = Settings(window_target_seconds=120, window_overlap_seconds=30, retry_base_delay=0.0)
    topics = extract_topics(t, HalfLLM(), settings, warnings=warns)
    assert [x.title for x in topics] == ["W1"]  # partial preserved
    assert warns and "window 2/3" in warns[0]


def test_merge_overlapping_dedupes_and_keeps_higher_score():
    dup_a = Topic(title="dup-low", summary="s", start=90.0, end=120.0, score=0.4)
    dup_b = Topic(title="dup-high", summary="s", start=100.0, end=130.0, score=0.8)
    distinct = Topic(title="other", summary="s", start=200.0, end=230.0, score=0.5)
    merged = merge_overlapping([dup_a, dup_b, distinct])
    assert len(merged) == 2
    # overlap merges and keeps the higher-score metadata, extending the span
    assert merged[0].title == "dup-high"
    assert merged[0].start == 90.0
    assert merged[0].end == 130.0
    assert merged[1].title == "other"


def test_merge_overlapping_keeps_distinct_adjacent_topics():
    # Two distinct subjects that only touch slightly at a window boundary
    # (overlap 5s of ~100s spans → 5%, well below the 50% threshold) must NOT
    # be collapsed into one — otherwise we'd over-deduplicate real topics.
    a = Topic(title="subject A", summary="s", start=0.0, end=100.0, score=0.6)
    b = Topic(title="subject B", summary="s", start=95.0, end=200.0, score=0.7)
    merged = merge_overlapping([a, b])
    assert [t.title for t in merged] == ["subject A", "subject B"]


def test_extract_topics_has_no_upper_limit():
    # 26 distinct, non-overlapping topics in a single window must all survive
    # (there is no longer a 10-20 cap).
    t = _transcript(52)  # 52 segments → indices 0..51
    topics_payload = [
        {"title": f"t{i}", "summary": "s", "start_index": 2 * i, "end_index": 2 * i + 1, "score": 0.5}
        for i in range(26)
    ]
    llm = FakeLLM({"record_topics": {"topics": topics_payload}})
    topics = extract_topics(t, llm)
    assert len(topics) == 26
    assert [tp.title for tp in topics] == [f"t{i}" for i in range(26)]


def test_anthropic_llm_forces_tool_and_parses_input():
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            block = SimpleNamespace(type="tool_use", name="record_topics", input={"topics": []})
            return SimpleNamespace(content=[block])

    fake_client = SimpleNamespace(messages=FakeMessages())
    llm = AnthropicLLM(client=fake_client)
    tool = {"name": "record_topics", "input_schema": {}}
    result = llm.select(system="sys", user="usr", tool=tool, tool_name="record_topics")

    assert result == {"topics": []}
    assert captured["tool_choice"] == {"type": "tool", "name": "record_topics"}
    assert captured["tools"] == [tool]


def test_openai_llm_forces_function_and_parses_arguments():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            fn = SimpleNamespace(name="record_topics", arguments=json.dumps({"topics": [1]}))
            call = SimpleNamespace(function=fn)
            msg = SimpleNamespace(tool_calls=[call])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    llm = OpenAILLM(settings=Settings(openai_model="gpt-4o"), client=fake_client)
    tool = {"name": "record_topics", "description": "d", "input_schema": {"type": "object"}}
    result = llm.select(system="sys", user="usr", tool=tool, tool_name="record_topics")

    assert result == {"topics": [1]}
    # converts to OpenAI's function format and forces the call
    assert captured["tool_choice"] == {"type": "function", "function": {"name": "record_topics"}}
    assert captured["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert captured["model"] == "gpt-4o"


def test_openrouter_llm_uses_base_url_key_and_model():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            fn = SimpleNamespace(name="record_topics", arguments=json.dumps({"topics": []}))
            call = SimpleNamespace(function=fn)
            msg = SimpleNamespace(tool_calls=[call])
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="or-key",
        openrouter_model="anthropic/claude-3.5-sonnet",
    )
    llm = OpenRouterLLM(settings=settings, client=fake_client)
    # the hooks point to the OpenRouter config
    assert llm._base_url() == "https://openrouter.ai/api/v1"
    assert llm._api_key() == "or-key"

    tool = {"name": "record_topics", "description": "d", "input_schema": {"type": "object"}}
    result = llm.select(system="s", user="u", tool=tool, tool_name="record_topics")
    assert result == {"topics": []}
    assert captured["model"] == "anthropic/claude-3.5-sonnet"


def test_openai_llm_raises_when_no_function_call():
    class FakeCompletions:
        def create(self, **kwargs):
            msg = SimpleNamespace(tool_calls=None)  # model without tool support
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    llm = OpenAILLM(settings=Settings(openai_model="model-without-tools"), client=fake_client)
    tool = {"name": "record_topics", "description": "d", "input_schema": {}}
    with pytest.raises(ToolCallError, match="tool use"):
        llm.select(system="s", user="u", tool=tool, tool_name="record_topics")


def test_anthropic_llm_raises_when_no_tool_use():
    class FakeMessages:
        def create(self, **kwargs):
            block = SimpleNamespace(type="text", text="sorry, I don't know how to use tools")
            return SimpleNamespace(content=[block])

    fake_client = SimpleNamespace(messages=FakeMessages())
    llm = AnthropicLLM(client=fake_client)
    tool = {"name": "record_topics", "input_schema": {}}
    with pytest.raises(ToolCallError):
        llm.select(system="s", user="u", tool=tool, tool_name="record_topics")


def test_get_llm_factory_selects_provider():
    assert isinstance(get_llm(Settings(llm_provider="anthropic")), AnthropicLLM)
    assert isinstance(get_llm(Settings(llm_provider="openai")), OpenAILLM)
    assert isinstance(get_llm(Settings(llm_provider="openrouter")), OpenRouterLLM)


def test_get_llm_rejects_unknown_provider():
    import pytest

    with pytest.raises(ValueError):
        get_llm(Settings(llm_provider="cohere"))
