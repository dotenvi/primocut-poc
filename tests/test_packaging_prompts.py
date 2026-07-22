"""The packaging prompt builders render the measured patterns + real examples into
text the model cites back. Pure string assembly — no LLM, no DB."""

from __future__ import annotations

from primocut.analysis import packaging_prompts as pp

CONTEXT = {
    "niche": {"project_name": "Cortes BR", "channels": 5, "outliers": 12,
              "format": "long"},
    "differential": [
        {"attribute": "thumb_expression_shock", "source": "thumbnail_llm",
         "verdict": "positive", "lift": 2.6, "freq_outlier": 0.78,
         "freq_control": 0.30, "n_outlier": 12},
    ],
    "conventions": [
        {"attribute": "title_has_person_name", "source": "title_llm",
         "freq": 0.95, "n": 40},
    ],
    "carries_value": {
        "person_names": [
            {"value": "Bolsonaro", "videos_outlier": 6, "videos_control": 2,
             "lift": 5.0},
        ],
    },
    "examples": [
        {"video_id": "vidAAA", "title": "O que ninguém contou", "band": "exceptional",
         "median_multiple": 20.6,
         "title_attrs": {"person_names": ["Bolsonaro"], "quoted_speech": "vou provar"},
         "thumb_attrs": {}},
    ],
    "flat": False,
}

CLIP = {"title": "A treta explicada", "summary": "resumo",
        "transcript_excerpt": "linha 1\nlinha 2"}


def test_pattern_index_assigns_typed_ids():
    ids = pp.pattern_index(CONTEXT)
    assert ids["D1"]["kind"] == "differential"
    assert ids["C1"]["kind"] == "convention"
    assert ids["V1"]["kind"] == "carries_value"
    assert ids["V1"]["value"] == "Bolsonaro"


def test_titles_prompt_carries_patterns_examples_and_k():
    ids = pp.pattern_index(CONTEXT)
    prompt = pp.build_titles_prompt(CLIP, CONTEXT, 5, ids)
    assert "Cortes BR" in prompt
    assert "A treta explicada" in prompt          # the clip content
    assert "thumb_expression_shock" in prompt      # the differential
    assert "[D1]" in prompt and "[V1]" in prompt   # citable ids
    assert "Bolsonaro" in prompt                   # the name that carries
    assert "vidAAA" in prompt                      # a real example, cited by id
    assert "exactly 5 titles" in prompt


def test_flat_niche_prompt_says_do_not_invent():
    flat = {**CONTEXT, "differential": [], "carries_value": {"person_names": []},
            "flat": True}
    ids = pp.pattern_index(flat)
    prompt = pp.build_titles_prompt(CLIP, flat, 3, ids)
    assert "flat" in prompt.lower()
    assert "do not invent" in prompt.lower()


def test_titles_tool_pins_k():
    tool = pp.titles_tool(5)
    items = tool["input_schema"]["properties"]["titles"]
    assert items["minItems"] == 5 and items["maxItems"] == 5


def test_audience_prompt_lists_topics_and_requests():
    topics = [{"id": 1, "title": "Guerra na Ucrânia", "summary": "s"}]
    requests = [{"type": "guest", "value": "Fulano", "videos": 3}]
    prompt = pp.build_audience_prompt(topics, requests)
    assert "[1] Guerra na Ucrânia" in prompt
    assert "Fulano" in prompt and "guest" in prompt
