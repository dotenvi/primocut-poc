"""Tests for the conditional distributions + the Wilson non-overlap guard.

The guard reports an attribute only when the 95% Wilson intervals of its outlier
and control frequencies separate — in either direction. The cases below pin the
spec's four scenarios (positive, inconclusive, anti-pattern, and the p=1/n=3 trap
that a Wald interval would wave through as absolute certainty).
"""

from __future__ import annotations

from primocut.metrics.patterns import conditional_patterns

Z = 1.96  # 95%


def _rows(n: int, hits: int, attr: str = "has_question") -> list[dict]:
    """`n` attribute rows, the first `hits` of them exhibiting `attr`."""
    return [{attr: i < hits} for i in range(n)]


def _get(reported, attr):
    return next((p for p in reported if p.attribute == attr), None)


def test_strong_lift_separates_as_positive():
    # 80% of outliers vs 30% of control — the intervals clear each other.
    reported = conditional_patterns(_rows(50, 40), _rows(50, 15), z=Z)
    p = _get(reported, "has_question")
    assert p is not None and p.verdict == "positive"
    assert p.freq_outlier == 0.8 and p.freq_control == 0.3
    assert round(p.lift, 3) == round(0.8 / 0.3, 3)
    assert p.n_outlier == 50 and p.n_control == 50
    assert p.ci_outlier[0] > p.ci_control[1]  # non-overlap, outlier above


def test_near_even_split_is_inconclusive():
    # 52% vs 48% — the intervals overlap heavily, nothing is reported.
    reported = conditional_patterns(_rows(50, 26), _rows(50, 24), z=Z)
    assert _get(reported, "has_question") is None


def test_rarer_in_outliers_reports_as_negative():
    # 10% of outliers vs 45% of control — an actionable anti-pattern to avoid.
    reported = conditional_patterns(_rows(50, 5), _rows(40, 18), z=Z)
    p = _get(reported, "has_question")
    assert p is not None and p.verdict == "negative"
    assert p.ci_outlier[1] < p.ci_control[0]  # non-overlap, outlier below


def test_p1_with_n3_does_not_pass():
    # 3/3 outliers exhibit it (Wald would give the zero-width interval [1, 1] and
    # falsely separate). Wilson's interval for n=3 is wide and overlaps the 80%
    # control, so the attribute is correctly withheld.
    reported = conditional_patterns(_rows(3, 3), _rows(50, 40), z=Z)
    assert _get(reported, "has_question") is None


def test_contaminated_denominator_hides_the_pattern():
    # 75% of outliers vs 40% of a proper (outlier-free) control — a real signal.
    outliers = _rows(40, 30)
    control = _rows(60, 24)
    good = _get(conditional_patterns(outliers, control, z=Z), "has_question")
    assert good is not None and good.verdict == "positive"

    # The legacy denominator put the outliers back INTO the comparison group,
    # pulling its rate up to 54% — the intervals now overlap and the signal is
    # crushed (exactly the bug that reported ~0.9× lift on every attribute).
    contaminated = list(outliers) + list(control)
    bad = _get(conditional_patterns(outliers, contaminated, z=Z), "has_question")
    assert bad is None
    assert good.lift > (0.75 / 0.54)  # the true lift is larger than the compressed one


def test_empty_group_returns_nothing():
    assert conditional_patterns([], [{"has_question": True}], z=Z) == []
    assert conditional_patterns([{"has_question": True}], [], z=Z) == []
