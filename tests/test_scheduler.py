"""Tests for signal_copier.scheduler.trigger — Scheduler + SignalSupervisor.

M6 ships the scheduler that drives the M2 state machine through the full
cascade. Tests use FakeBroker + RecordingNotifier (from _scheduler_fixtures)
and a real asyncio.Queue + Scheduler.run() loop. Sub-second timing tests
exercise the actual call_at scheduling path.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import pytest

from signal_copier.config import Config
from signal_copier.scheduler.trigger import Scheduler, compute_target_monotonic
from tests._scheduler_fixtures import FakeBroker, RecordingNotifier


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


class _NoOpStateStore:
    """Minimal StateStore stub: every method returns None or False.
    The Scheduler tests don't exercise any state-machine logic, so the
    supervisor stub in this task doesn't call any state_store methods.
    """

    async def get_signal(self, signal_id: str) -> Any:
        return None

    async def update_signal_state(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_stage_placed(self, *args: Any, **kwargs: Any) -> str:
        return "stub-trade-id"

    async def record_stage_result(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def update_daily_summary(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def get_daily_summary(self, on_date: Any) -> Any:
        return None


def _make_scheduler() -> tuple[Scheduler, asyncio.Queue, FakeBroker, RecordingNotifier]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    broker = FakeBroker()
    notifier = RecordingNotifier()
    state_store = _NoOpStateStore()
    scheduler = Scheduler(
        queue=queue,
        broker=broker,
        state_store=state_store,  # type: ignore[arg-type]
        notifier=notifier,
        config=Config(),
    )
    return scheduler, queue, broker, notifier


@pytest.mark.asyncio
async def test_scheduler_drains_queue_and_spawns_supervisor() -> None:
    """Pushing one signal starts one supervisor task tracked by the
    scheduler. The task completes (or raises) and is removed from the
    active set."""

    # We replace the scheduler's `_supervise` with a no-op that records
    # invocation but doesn't spawn a real SignalSupervisor (the real
    # supervisor comes in later tasks).
    scheduler, queue, _, _ = _make_scheduler()

    supervisor_invocations: list[str] = []

    async def fake_supervise(signal: Any) -> None:
        supervisor_invocations.append(signal.signal_id)

    scheduler._supervise = fake_supervise  # type: ignore[method-assign]

    from tests._scheduler_fixtures import make_signal_with_future_trigger

    signal = make_signal_with_future_trigger(trigger_in_seconds=0.05)
    await queue.put(signal)

    # Run scheduler briefly; cancel after supervisor completes.
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.3)  # let supervisor finish
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert supervisor_invocations == [signal.signal_id]
    assert scheduler.active_task_count == 0


@pytest.mark.asyncio
async def test_scheduler_active_task_count_tracks_in_flight_supervisors() -> None:
    """While a supervisor is running, active_task_count is 1; after it
    completes, the count is 0 (the done-callback removes it)."""

    scheduler, queue, _, _ = _make_scheduler()

    supervise_started = asyncio.Event()
    supervise_release = asyncio.Event()

    async def blocking_supervise(signal: Any) -> None:
        supervise_started.set()
        await supervise_release.wait()

    scheduler._supervise = blocking_supervise  # type: ignore[method-assign]

    from tests._scheduler_fixtures import make_signal_with_future_trigger

    signal = make_signal_with_future_trigger(trigger_in_seconds=0.05)
    await queue.put(signal)

    task = asyncio.create_task(scheduler.run())

    await supervise_started.wait()
    assert scheduler.active_task_count == 1

    supervise_release.set()
    await asyncio.sleep(0.05)  # let supervisor finish + done callback fire
    assert scheduler.active_task_count == 0

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_scheduler_cancellation_propagates_to_supervisors() -> None:
    """Cancelling the scheduler cancels all in-flight supervisor tasks."""

    scheduler, queue, _, _ = _make_scheduler()

    supervise_started = asyncio.Event()
    supervise_cancelled = asyncio.Event()

    async def long_supervise(signal: Any) -> None:
        supervise_started.set()
        try:
            await asyncio.sleep(60)  # effectively forever
        except asyncio.CancelledError:
            supervise_cancelled.set()
            raise

    scheduler._supervise = long_supervise  # type: ignore[method-assign]

    from tests._scheduler_fixtures import make_signal_with_future_trigger

    signal = make_signal_with_future_trigger(trigger_in_seconds=0.05)
    await queue.put(signal)

    task = asyncio.create_task(scheduler.run())
    await supervise_started.wait()

    task.cancel()
    # Give the scheduler's CancelledError handler time to cancel supervisors.
    await asyncio.wait_for(supervise_cancelled.wait(), timeout=2.0)
