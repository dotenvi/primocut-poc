"""Tests for the per-project conditional distributions + minimum-occurrence guard."""

from __future__ import annotations

from primocut.metrics.patterns import conditional_patterns


def _attr(question: bool, brackets: bool) -> dict:
    return {"has_question": question, "has_brackets_or_pipe": brackets}


def test_min_occurrence_guard_suppresses_small_sample_pattern():
    # 100 population videos, 40% have a question mark.
    population = [_attr(i < 40, False) for i in range(100)]
    # Only 8 outliers, all with a question mark: a strong-looking but tiny sample.
    outliers = [_attr(True, False) for _ in range(8)]

    reported = conditional_patterns(population, outliers, min_occurrence=15)
    assert all(p.attribute != "has_question" for p in reported)  # suppressed

    # With enough outliers exhibiting it, the same pattern IS reported with lift.
    outliers = [_attr(True, False) for _ in range(20)]
    reported = conditional_patterns(population, outliers, min_occurrence=15)
    q = next(p for p in reported if p.attribute == "has_question")
    assert q.outlier_occurrences == 20
    assert q.outlier_rate == 1.0 and q.population_rate == 0.4
    assert q.lift == 2.5


def test_empty_inputs_return_no_patterns():
    assert conditional_patterns([], [{"has_question": True}], min_occurrence=1) == []
    assert conditional_patterns([{"has_question": True}], [], min_occurrence=1) == []
