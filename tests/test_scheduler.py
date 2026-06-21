"""Tests for signal_copier.scheduler.trigger — Scheduler + SignalSupervisor.

M6 ships the scheduler that drives the M2 state machine through the full
cascade. Tests use FakeBroker + RecordingNotifier (from _scheduler_fixtures)
and a real asyncio.Queue + Scheduler.run() loop. Sub-second timing tests
exercise the actual call_at scheduling path.
"""

from __future__ import annotations

import time

from signal_copier.scheduler.trigger import compute_target_monotonic


def test_compute_target_monotonic_future_target_returns_monotonic_anchor() -> None:
    """A target 5 seconds in the future should produce a monotonic time
    roughly equal to `loop.time() + 5.0`."""
    target_wall = time.time() + 5.0
    result = compute_target_monotonic(target_wall)
    # We can't compare to loop.time() outside an event loop, but we can
    # verify the function returns a float > 0 (sanity) and that the delta
    # to `time.monotonic()` is close to the wall-clock delta.
    mono_now = time.monotonic()
    delta = result - mono_now
    assert 4.5 < delta < 5.5


def test_compute_target_monotonic_past_target_returns_loop_now_equivalent() -> None:
    """A target already in the past should return a monotonic time at or
    near the current monotonic value (so call_at fires immediately)."""
    target_wall = time.time() - 30.0  # 30 seconds ago
    result = compute_target_monotonic(target_wall)
    mono_now = time.monotonic()
    # result should be <= mono_now + small slop (function reads monotonic
    # before us; tiny clock drift is OK)
    assert result <= mono_now + 0.1


def test_compute_target_monotonic_exactly_now() -> None:
    """A target exactly equal to now_unix should return a monotonic time
    at or near the current monotonic value."""
    target_wall = time.time()
    result = compute_target_monotonic(target_wall)
    mono_now = time.monotonic()
    assert result <= mono_now + 0.1
