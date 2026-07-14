"""Phase 2 metrics engine (deterministic, no LLM).

All derived scores — outlier score, views-per-hour, resurgence, conditional
distributions — are computed ON READ over the collected snapshots and are NEVER
stored (PRIMOCUT.md guideline 10). The only persisted artifacts are the syntactic
title attributes (`metrics.attributes`) and the packaging before/after impact
(`metrics.impact`), both mandated by the data model.

`metrics.core` is intentionally DB-free (plain dataclasses in, dataclasses out) so
the methodology can be unit-tested directly; `metrics.service` adapts DB rows to it
and assembles the dashboard payloads.
"""
