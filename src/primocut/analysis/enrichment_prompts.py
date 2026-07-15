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

# ------------------------- comment synthesis (opt-in) -------------------------
#
# The golden rule, applied to quotes: the comments reach the model INDEXED and it
# returns only `example_index`, never comment text. `metrics.comments` resolves
# each index back to the stored comment and clamps it, so the model cannot invent
# words the audience never wrote — exactly how `resolve_span` protects timestamps.

COMMENT_SYNTHESIS_SYSTEM = (
    "You read the top comments of a YouTube video for a clip editor studying the "
    "demand in a niche. Report only what the comments actually say. A request "
    "counts only when a comment explicitly asks for it — never infer a wish from "
    "enthusiasm, and never turn a compliment into a request. The comments are "
    "usually in PT-BR: keep themes and requested names in the comments' own "
    "language. Quote nothing: to point at a comment, give its index [i]. If there "
    "are no requests, return an empty list rather than a plausible-looking one."
)

COMMENT_SYNTHESIS_TOOL = {
    "name": "record_comment_synthesis",
    "description": "Record what a YouTube video's top comments are saying.",
    "input_schema": {
        "type": "object",
        "properties": {
            "themes": {
                "type": "array",
                "maxItems": 8,
                "description": "Recurring subjects in the comments, most discussed first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme": {
                            "type": "string",
                            "description": (
                                "The recurring subject, in the comments' own language."
                            ),
                        },
                        "mentions": {
                            "type": "integer",
                            "description": "How many of the supplied comments express it.",
                        },
                        "example_index": {
                            "type": "integer",
                            "description": (
                                "The index [i] of one supplied comment that expresses "
                                "it. Never write the comment's text."
                            ),
                        },
                    },
                    "required": ["theme", "mentions", "example_index"],
                },
            },
            "requests": {
                "type": "array",
                "maxItems": 10,
                "description": (
                    "Things the audience explicitly asks for. Only what a comment "
                    "actually asks for — never an inferred wish."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["guest", "topic", "format", "other"],
                            "description": (
                                "guest = a person to bring on, topic = a subject to "
                                "cover, format = how to make it (length, editing, "
                                "series), other = anything else asked for."
                            ),
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "The thing asked for, named as the audience names it."
                            ),
                        },
                        "mentions": {
                            "type": "integer",
                            "description": "How many of the supplied comments ask for it.",
                        },
                        "example_index": {
                            "type": "integer",
                            "description": (
                                "The index [i] of one supplied comment asking for it. "
                                "Never write the comment's text."
                            ),
                        },
                    },
                    "required": ["type", "value", "mentions", "example_index"],
                },
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "mixed", "negative", "neutral"],
                "description": "The overall reaction across the supplied comments.",
            },
            "sentiment_note": {
                "type": "string",
                "description": "One short sentence on the overall reaction.",
            },
        },
        "required": ["themes", "requests", "sentiment", "sentiment_note"],
    },
}
