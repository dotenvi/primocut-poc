"""Testes do módulo transversal de resiliência (retry/backoff/classificação)."""

from __future__ import annotations

import urllib.error

import pytest

from primocut.resilience import RetryPolicy, is_transient, retry


class _Transient(Exception):
    status_code = 503


class _Permanent(Exception):
    status_code = 400


def _recorder():
    delays: list[float] = []
    return delays, lambda d: delays.append(d)


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Transient()
        return "ok"

    delays, sleep = _recorder()
    result = retry(fn, policy=RetryPolicy(attempts=5, base_delay=1.0), is_retryable=is_transient, sleep=sleep)
    assert result == "ok"
    assert calls["n"] == 3
    assert delays == [1.0, 2.0]  # backoff exponencial


def test_retry_reraises_non_retryable_immediately():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Permanent()

    delays, sleep = _recorder()
    with pytest.raises(_Permanent):
        retry(fn, policy=RetryPolicy(attempts=5), is_retryable=is_transient, sleep=sleep)
    assert calls["n"] == 1  # não repetiu
    assert delays == []


def test_retry_exhausts_attempts_and_reraises():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Transient()

    delays, sleep = _recorder()
    with pytest.raises(_Transient):
        retry(fn, policy=RetryPolicy(attempts=3, base_delay=1.0), is_retryable=is_transient, sleep=sleep)
    assert calls["n"] == 3
    assert delays == [1.0, 2.0]  # dormiu entre as 3 tentativas (2 esperas)


def test_backoff_is_capped_by_max_delay():
    def fn():
        raise _Transient()

    delays, sleep = _recorder()
    with pytest.raises(_Transient):
        retry(fn, policy=RetryPolicy(attempts=5, base_delay=10.0, max_delay=15.0),
              is_retryable=is_transient, sleep=sleep)
    assert delays == [10.0, 15.0, 15.0, 15.0]  # 10, 20→15, 40→15, 80→15


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_Transient(), True),      # status 503
        (_Permanent(), False),     # status 400
        (ConnectionError(), True),
        (TimeoutError(), True),
        (urllib.error.URLError("x"), True),
        (ValueError("x"), False),
        (FileNotFoundError(), False),  # binário ausente NÃO é transitório
    ],
)
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_is_transient_by_class_name():
    class RateLimitError(Exception):
        pass

    assert is_transient(RateLimitError()) is True
