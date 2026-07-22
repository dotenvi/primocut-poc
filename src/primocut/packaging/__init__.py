"""Phase 3 AI packaging of a cut (opt-in — costs LLM, guideline 9).

Per-clip title candidates + thumbnail brief + candidate-frame ranking, driven by
the LIVE's project Phase 2 patterns (`metrics.packaging_signals`). Frame extraction
is deterministic (`render.frames`, no LLM); only titles/brief/ranking and the
audience match cost a model call.
"""
