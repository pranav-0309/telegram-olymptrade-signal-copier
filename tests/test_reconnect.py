"""Tests for broker/reconnect.py helpers."""

from __future__ import annotations

import pytest

from signal_copier.broker.reconnect import compute_backoff_seconds


def test_compute_backoff_seconds_exponential_growth() -> None:
    """Attempt 0,1,2,3,4 → ~1,2,4,8,16 with jitter ±10%."""
    for attempt, expected in enumerate((1.0, 2.0, 4.0, 8.0, 16.0)):
        result = compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.0)
        assert result == pytest.approx(expected, abs=0.01), f"attempt={attempt} got {result}"


def test_compute_backoff_seconds_caps_at_cap_arg() -> None:
    """Large attempts must not exceed the cap."""
    for attempt in (5, 10, 20, 100):
        assert compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.0) == 30.0


def test_compute_backoff_seconds_within_jitter_range() -> None:
    """With jitter=0.1 the result must be within ±10% of the base value (before cap)."""
    for attempt, base_value in enumerate((1.0, 2.0, 4.0, 8.0)):
        for _ in range(20):  # sample to catch jitter randomness
            result = compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.1)
            assert (
                abs(result - base_value) <= base_value * 0.1 + 0.001
            ), f"attempt={attempt} result={result} base={base_value}"
