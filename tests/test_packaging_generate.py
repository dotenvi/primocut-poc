"""Phase 3 AI packaging generation core (LLM faked, no network).

Covers: K respected + pattern citations validated (a bogus one dropped, none
invented), the structured brief with references clamped to real examples, frame
ranking resolving indices to real files (golden rule for stills), and the audience
match clamping topic ids + degrading to no-LLM when the niche has no requests.
"""

from __future__ import annotations

from primocut.config import Settings
from primocut.packaging import generate as gen

SETTINGS = Settings()

CONTEXT = {
    "niche": {"project_name": "N", "channels": 3, "outliers": 10, "format": "long"},
    "differential": [
        {"attribute": "thumb_expression_shock", "source": "thumbnail_llm",
         "verdict": "positive", "lift": 2.6, "freq_outlier": 0.8, "freq_control": 0.3,
         "n_outlier": 10},
    ],
    "conventions": [
        {"attribute": "title_has_person_name", "source": "title_llm", "freq": 0.95,
         "n": 30},
    ],
    "carries_value": {
        "person_names": [
            {"value": "Bolsonaro", "videos_outlier": 6, "videos_control": 1,
             "lift": 6.0},
        ],
    },
    "examples": [
        {"video_id": "vidREAL", "title": "Real outlier", "band": "strong",
         "median_multiple": 12.0, "title_attrs": {}, "thumb_attrs": {}},
    ],
    "flat": False,
}
CLIP = {"title": "Clip", "summary": "s", "transcript_excerpt": "line"}


class FakeLLM:
    """Records each call and replays a scripted response keyed by tool_name."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def select(self, *, system, user, tool, tool_name, images=None, model=None):
        self.calls.append({"tool_name": tool_name, "user": user, "images": images,
                           "model": model})
        return self.responses[tool_name]


# ------------------------- titles -------------------------


def test_titles_respects_k_and_validates_citations():
    # Model returns 5 titles; one cites a real pattern (D1), one cites a bogus one
    # (D9) which must be dropped, none invented.
    responses = {"propose_titles": {"titles": [
        {"title": f"T{i}", "length_chars": 99,
         "patterns_applied": [{"kind": "differential", "reference": "D1"},
                              {"kind": "differential", "reference": "D9"}],
         "rationale": "r"}
        for i in range(5)
    ]}}
    llm = FakeLLM(responses)
    out = gen.generate_titles(CLIP, CONTEXT, 5, llm=llm, settings=SETTINGS)

    assert len(out["titles"]) == 5
    first = out["titles"][0]
    # D1 kept, D9 dropped — no invented pattern survives.
    assert [c["reference"] for c in first["patterns_applied"]] == ["D1"]
    # length_chars is recomputed, not trusted (was 99 in the fake).
    assert first["length_chars"] == len(first["title"])
    # K flows into the forced tool schema.
    assert gen.pp.titles_tool(5)["input_schema"]["properties"]["titles"]["maxItems"] == 5


def test_titles_truncates_an_overlong_answer_to_k():
    responses = {"propose_titles": {"titles": [
        {"title": f"T{i}", "length_chars": 3, "patterns_applied": [], "rationale": ""}
        for i in range(9)  # model over-produced
    ]}}
    out = gen.generate_titles(CLIP, CONTEXT, 5, llm=FakeLLM(responses), settings=SETTINGS)
    assert len(out["titles"]) == 5


def test_flat_niche_titles_allow_empty_citations():
    flat = {**CONTEXT, "differential": [], "carries_value": {"person_names": []},
            "flat": True}
    responses = {"propose_titles": {"titles": [
        {"title": "Só o assunto", "length_chars": 12, "patterns_applied": [],
         "rationale": "flat niche"}
    ]}}
    out = gen.generate_titles(CLIP, flat, 1, llm=FakeLLM(responses), settings=SETTINGS)
    assert out["flat"] is True
    assert out["titles"][0]["patterns_applied"] == []


# ------------------------- brief -------------------------


def test_brief_is_structured_and_clamps_references():
    responses = {"record_thumbnail_brief": {
        "composition": {"face_count": 1, "expression": "shock", "framing": "single"},
        "text": {"word_count": 3, "suggestion": "OLHA ISSO"},
        "colors": {"palette": ["red", "black"], "contrast": "high"},
        "graphics": {"arrow": True, "circle_highlight": False, "note": "n"},
        "references": [{"video_id": "vidREAL", "why": "used shock"},
                       {"video_id": "vidFAKE", "why": "invented"}],
        "pattern_notes": "D1",
    }}
    out = gen.generate_brief(CLIP, CONTEXT, llm=FakeLLM(responses), settings=SETTINGS)
    assert out["composition"]["expression"] == "shock"
    assert out["text"]["word_count"] == 3
    # The fabricated reference is dropped; only the real example survives.
    assert [r["video_id"] for r in out["references"]] == ["vidREAL"]


# ------------------------- frame ranking -------------------------


def test_rank_frames_resolves_indices_and_clamps():
    frame_names = ["frame-001.jpg", "frame-002.jpg", "frame-003.jpg"]
    images = [("image/jpeg", b"\xff\xd8\xff")] * 3
    responses = {"rank_frames": {
        "rankings": [
            {"frame": 1, "score": 0.9, "expression": 0.8, "sharpness": 0.7,
             "framing": 0.6, "fits_brief": True, "note": "best"},
            {"frame": 9, "score": 0.5, "expression": 0.5, "sharpness": 0.5,
             "framing": 0.5, "fits_brief": False, "note": "out of range"},
        ],
        "best_frame": 1,
    }}
    llm = FakeLLM(responses)
    out = gen.rank_frames(frame_names, {"composition": {}}, images, llm=llm,
                          settings=SETTINGS)
    # The out-of-range index is clamped to a real frame (2), not dropped silently
    # into an invalid file reference.
    assert out["rankings"][0]["filename"] == "frame-002.jpg"
    assert out["rankings"][1]["frame"] == 2  # clamped from 9
    assert out["best_frame"] == 1 and out["best_filename"] == "frame-002.jpg"
    # The vision pass carried the images.
    assert llm.calls[0]["images"] == images


# ------------------------- audience match -------------------------


def test_audience_match_without_requests_calls_no_llm():
    llm = FakeLLM({})  # would KeyError if called
    topics = [{"id": 1, "title": "T", "summary": ""}]
    out = gen.match_audience(topics, [], llm=llm, settings=SETTINGS)
    assert out == {}
    assert llm.calls == []


def test_audience_match_clamps_topic_ids():
    responses = {"match_audience": {"matches": [
        {"topic_id": 1, "request_type": "guest", "request_value": "Fulano",
         "confidence": "strong"},
        {"topic_id": 99, "request_type": "topic", "request_value": "ghost",
         "confidence": "weak"},  # not a real topic → dropped
    ]}}
    topics = [{"id": 1, "title": "T", "summary": ""}]
    requests = [{"type": "guest", "value": "Fulano", "videos": 3}]
    out = gen.match_audience(topics, requests, llm=FakeLLM(responses), settings=SETTINGS)
    assert set(out.keys()) == {"1"}
    assert out["1"][0]["value"] == "Fulano"


def test_content_hash_changes_with_patterns_and_k():
    h1 = gen.content_hash("title", CLIP, CONTEXT, k=5)
    h2 = gen.content_hash("title", CLIP, CONTEXT, k=3)  # different K
    flat = {**CONTEXT, "differential": []}
    h3 = gen.content_hash("title", CLIP, flat, k=5)     # different patterns
    assert h1 != h2 and h1 != h3
    assert h1 == gen.content_hash("title", CLIP, CONTEXT, k=5)  # stable
