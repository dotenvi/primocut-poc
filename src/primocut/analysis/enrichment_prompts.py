"""Prompts and tool schemas for the opt-in AI packaging enrichment.

Same structured-output technique as the topic analysis: a forced tool call whose
schema IS the contract, so the model can only answer in the shape we persist —
no free text to parse, no invented fields.

The schemas are written in Anthropic shape (`name`/`description`/`input_schema`)
because `OpenAILLM.select()` converts that shape into OpenAI's function format,
which keeps the enrichment provider-agnostic with no branching here.

Every field is an OBSERVATION, never a judgement: the model reports what is in
the image/title, and the code derives the boolean facets the pattern
distributions consume (see `metrics.enrichment`). Thresholds therefore stay
auditable instead of living in the model's head.
"""

from __future__ import annotations

THUMBNAIL_SYSTEM = (
    "You analyse YouTube thumbnails for a clip editor studying what works in a "
    "niche. Report only what is actually visible in the image — never guess at "
    "the video's content, and never infer from what you think the video is about. "
    "If an attribute does not apply, use the neutral/empty value (0, false, "
    "\"none\" or an empty string) rather than a plausible-looking one."
)

THUMBNAIL_TOOL = {
    "name": "record_thumbnail_attributes",
    "description": "Record the visual packaging attributes of a YouTube thumbnail.",
    "input_schema": {
        "type": "object",
        "properties": {
            "face_count": {
                "type": "integer",
                "description": "Number of clearly visible human faces. 0 if none.",
            },
            "dominant_expression": {
                "type": "string",
                "enum": ["none", "neutral", "happy", "shock", "anger", "sadness", "fear", "disgust"],
                "description": (
                    "Facial expression of the most prominent face. "
                    "Use 'none' when there is no visible face."
                ),
            },
            "has_text": {
                "type": "boolean",
                "description": "Whether any text is overlaid on the image.",
            },
            "text_word_count": {
                "type": "integer",
                "description": "Number of words of overlaid text. 0 when there is none.",
            },
            "text": {
                "type": "string",
                "description": (
                    "The overlaid text transcribed verbatim, preserving its casing. "
                    "Empty string when there is no text."
                ),
            },
            "dominant_colors": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "string",
                    "enum": ["red", "orange", "yellow", "green", "blue", "purple",
                             "pink", "brown", "black", "white", "gray"],
                },
                "description": "Up to 3 dominant colors, most dominant first.",
            },
            "contrast": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Overall visual contrast between the subject and the background.",
            },
            "has_arrow": {
                "type": "boolean",
                "description": "Whether an arrow graphic points at something.",
            },
            "has_circle_or_highlight": {
                "type": "boolean",
                "description": (
                    "Whether a circle, ring or highlight is drawn to call out part "
                    "of the image."
                ),
            },
            "is_confrontation_framing": {
                "type": "boolean",
                "description": (
                    "Whether two or more subjects are framed against each other "
                    "(face-to-face, split screen, versus)."
                ),
            },
        },
        "required": ["face_count", "dominant_expression", "has_text", "text_word_count",
                     "text", "dominant_colors", "contrast", "has_arrow",
                     "has_circle_or_highlight", "is_confrontation_framing"],
    },
}

TITLE_SYSTEM = (
    "You analyse YouTube video titles for a clip editor studying what works in a "
    "niche. The titles are usually in PT-BR. Report only what the title itself "
    "says — do not speculate about the video's content. When an attribute is "
    "absent, return the empty value (an empty list or an empty string) rather "
    "than a plausible-looking guess."
)

TITLE_TOOL = {
    "name": "record_title_attributes",
    "description": "Record the semantic attributes of a YouTube video title.",
    "input_schema": {
        "type": "object",
        "properties": {
            "person_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Names of real people named in the title, exactly as written. "
                    "Empty list when the title names nobody."
                ),
            },
            "topic": {
                "type": "string",
                "description": (
                    "The subject of the video in at most 4 words, in the title's "
                    "own language."
                ),
            },
            "tone": {
                "type": "string",
                "enum": ["neutral", "hype", "outrage", "humor", "curiosity", "drama",
                         "informative"],
                "description": "The dominant tone of the title.",
            },
            "quoted_speech": {
                "type": "string",
                "description": (
                    "The line of speech the title quotes, verbatim. Empty string "
                    "when the title quotes nobody."
                ),
            },
        },
        "required": ["person_names", "topic", "tone", "quoted_speech"],
    },
}
