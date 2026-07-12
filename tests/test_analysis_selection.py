"""Testes da análise por LLM.

Foco central: provar que os timestamps são SEMPRE derivados dos segmentos da
transcrição (seleção de índices), nunca inventados pelo LLM. Tudo com um LLM
fake — sem rede.
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
from primocut.analysis.moments import extract_moments
from primocut.analysis.topics import extract_topics
from primocut.models import CutMoment, Segment, Topic, Transcript


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
    """Retorna payloads canônicos por nome de tool."""

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
    # timestamps vêm dos segmentos, não do LLM
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
    # índices absurdos são clampados aos limites reais da transcrição
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
                    "isso não é um objeto",  # item inválido (string)
                    {"title": "sem indices", "summary": "s", "score": 0.5},  # faltam índices
                ]
            }
        }
    )
    topics = extract_topics(t, llm)
    # só o item válido sobrevive; os ruins são ignorados (não derruba a análise)
    assert [tp.title for tp in topics] == ["bom"]

    # coerce_items lida com dict indexado {"0": {...}} em vez de lista
    items = coerce_items({"moments": {"0": {"a": 1}, "1": {"b": 2}}}, "moments")
    assert items == [{"a": 1}, {"b": 2}]


class _Transient(Exception):
    status_code = 503


def test_extract_topics_retries_transient_then_succeeds():
    t = _transcript()  # 1 janela
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
    assert llm.n == 3  # 2 falhas transitórias + 1 sucesso


def test_extract_topics_stops_on_permanent_keeps_partial_and_warns():
    t = _transcript(30)  # com janela pequena => 3 janelas
    good = {"topics": [{"title": "W1", "summary": "s", "start_index": 0, "end_index": 1, "score": 0.7}]}

    class HalfLLM:
        def __init__(self):
            self.n = 0

        def select(self, **kwargs):
            self.n += 1
            if self.n == 1:
                return good
            raise RuntimeError("crédito acabou")  # permanente (sem status_code)

    warns: list[str] = []
    settings = Settings(window_target_seconds=120, window_overlap_seconds=30, retry_base_delay=0.0)
    topics = extract_topics(t, HalfLLM(), settings, warnings=warns)
    assert [x.title for x in topics] == ["W1"]  # parcial preservado
    assert warns and "janela 2/3" in warns[0]


def test_extract_moments_resolves_indices():
    t = _transcript()
    llm = FakeLLM(
        {
            "record_moments": {
                "moments": [
                    {"title": "piada", "reason": "engraçado", "start_index": 1, "end_index": 1, "score": 0.95}
                ]
            }
        }
    )
    moments = extract_moments(t, llm)
    assert len(moments) == 1
    assert moments[0].start == 10.0
    assert moments[0].end == 20.0


def test_merge_overlapping_dedupes_and_keeps_higher_score():
    dup_a = Topic(title="dup-low", summary="s", start=90.0, end=120.0, score=0.4)
    dup_b = Topic(title="dup-high", summary="s", start=100.0, end=130.0, score=0.8)
    distinct = Topic(title="outro", summary="s", start=200.0, end=230.0, score=0.5)
    merged = merge_overlapping([dup_a, dup_b, distinct])
    assert len(merged) == 2
    # sobreposição funde e mantém metadados do maior score, estendendo o span
    assert merged[0].title == "dup-high"
    assert merged[0].start == 90.0
    assert merged[0].end == 130.0
    assert merged[1].title == "outro"


def test_merge_overlapping_moments_type():
    a = CutMoment(title="a", reason="r", start=0.0, end=10.0, score=0.5)
    b = CutMoment(title="b", reason="r", start=5.0, end=15.0, score=0.9)
    merged = merge_overlapping([a, b])
    assert len(merged) == 1
    assert merged[0].title == "b"


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
    # converte para o formato de função da OpenAI e força a chamada
    assert captured["tool_choice"] == {"type": "function", "function": {"name": "record_topics"}}
    assert captured["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert captured["model"] == "gpt-4o"


def test_openrouter_llm_uses_base_url_key_and_model():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            fn = SimpleNamespace(name="record_moments", arguments=json.dumps({"moments": []}))
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
    # os hooks apontam para a config do OpenRouter
    assert llm._base_url() == "https://openrouter.ai/api/v1"
    assert llm._api_key() == "or-key"

    tool = {"name": "record_moments", "description": "d", "input_schema": {"type": "object"}}
    result = llm.select(system="s", user="u", tool=tool, tool_name="record_moments")
    assert result == {"moments": []}
    assert captured["model"] == "anthropic/claude-3.5-sonnet"


def test_openai_llm_raises_when_no_function_call():
    class FakeCompletions:
        def create(self, **kwargs):
            msg = SimpleNamespace(tool_calls=None)  # modelo sem suporte a tools
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    llm = OpenAILLM(settings=Settings(openai_model="modelo-sem-tools"), client=fake_client)
    tool = {"name": "record_topics", "description": "d", "input_schema": {}}
    with pytest.raises(ToolCallError, match="tool use"):
        llm.select(system="s", user="u", tool=tool, tool_name="record_topics")


def test_anthropic_llm_raises_when_no_tool_use():
    class FakeMessages:
        def create(self, **kwargs):
            block = SimpleNamespace(type="text", text="desculpa, não sei usar ferramentas")
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
