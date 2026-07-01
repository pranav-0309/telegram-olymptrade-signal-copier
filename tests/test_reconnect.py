"""Tests for broker/reconnect.py helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from signal_copier.broker.base import BrokerAuthError
from signal_copier.broker.reconnect import compute_backoff_seconds, with_retry


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


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_try_no_callbacks_called() -> None:
    """If op succeeds immediately, no retry callbacks fire."""
    op = AsyncMock(return_value=None)
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    await with_retry(op, op_name="test", on_retry=on_retry, on_exhausted=on_exhausted)

    op.assert_awaited_once()
    on_retry.assert_not_awaited()
    on_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_retry_retries_on_broker_auth_error_then_succeeds() -> None:
    """First two calls raise BrokerAuthError; third succeeds; on_retry fires 2x."""
    op = AsyncMock(side_effect=[BrokerAuthError("e1"), BrokerAuthError("e2"), None])
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    await with_retry(op, op_name="mt5.initialize", on_retry=on_retry, on_exhausted=on_exhausted)

    assert op.await_count == 3
    assert on_retry.await_count == 2
    assert on_exhausted.await_count == 0


@pytest.mark.asyncio
async def test_with_retry_exhausts_then_raises_broker_auth_error(monkeypatch) -> None:
    """Five consecutive failures → BrokerAuthError + on_exhausted called once."""
    # Skip real backoff sleeps so the test runs in ms, not 15+ seconds.
    sleep_mock = AsyncMock()
    monkeypatch.setattr("signal_copier.broker.reconnect.asyncio.sleep", sleep_mock)

    op = AsyncMock(side_effect=[BrokerAuthError(f"e{i}") for i in range(5)])
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    with pytest.raises(BrokerAuthError, match="mt5.initialize failed after 5 attempts"):
        await with_retry(
            op,
            op_name="mt5.initialize",
            on_retry=on_retry,
            on_exhausted=on_exhausted,
            max_attempts=5,
        )

    assert op.await_count == 5
    assert on_retry.await_count == 4
    on_exhausted.assert_awaited_once()
    on_exhausted.assert_awaited_with(attempts=5, total_downtime_seconds=pytest.approx(0.0, abs=5.0))


@pytest.mark.asyncio
async def test_with_retry_re_raises_non_retryable_exception_immediately() -> None:
    """ValueError (not BrokerAuthError/OSError) is re-raised without retry."""
    op = AsyncMock(side_effect=ValueError("boom"))
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    with pytest.raises(ValueError, match="boom"):
        await with_retry(op, op_name="test", on_retry=on_retry, on_exhausted=on_exhausted)

    op.assert_awaited_once()
    on_retry.assert_not_awaited()
    on_exhausted.assert_not_awaited()
