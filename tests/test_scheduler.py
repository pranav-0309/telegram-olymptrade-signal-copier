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
from decimal import Decimal
from typing import Any

import pytest

from signal_copier.config import Config
from signal_copier.scheduler.trigger import Scheduler, compute_target_monotonic
from tests._scheduler_fixtures import (
    FakeBroker,
    FakeStateStore,
    RecordingNotifier,
    make_daily_summary,
    make_signal_with_future_trigger,
)


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


# --- SignalSupervisor intake tests (Task 8) ------------------------------


async def _no_op_drive_cascade(state: Any) -> None:
    return None


def _make_supervisor(
    *,
    state_store: FakeStateStore,
    broker: FakeBroker | None = None,
    notifier: RecordingNotifier | None = None,
    config: Config | None = None,
    trigger_in_seconds: float = 0.05,
    signal_id: str = "test-sig-1",
):
    """Build a SignalSupervisor ready to run. We DON'T run the scheduler;
    we run the supervisor directly via `await supervisor.run()`."""
    from signal_copier.scheduler.trigger import SignalSupervisor

    broker = broker or FakeBroker()
    notifier = notifier or RecordingNotifier()
    config = config or Config()
    signal = make_signal_with_future_trigger(
        trigger_in_seconds=trigger_in_seconds,
        signal_id=signal_id,
    )
    supervisor = SignalSupervisor(
        signal=signal,
        broker=broker,
        state_store=state_store,  # type: ignore[arg-type]
        notifier=notifier,
        config=config,
    )
    return supervisor, signal, broker, notifier


@pytest.mark.asyncio
async def test_supervisor_emits_on_signal_received_for_fresh_signal() -> None:
    """A fresh signal (not in signals table) gets on_signal_received."""
    state_store = FakeStateStore()
    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
    )
    # Stub the cascade so the supervisor exits after intake (Task 9 wires
    # the real _drive_cascade; here we patch it to a no-op).
    supervisor._drive_cascade = _no_op_drive_cascade  # type: ignore[method-assign]

    await supervisor.run()

    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_received" in method_names
    assert method_names.index("on_signal_received") == 0  # first event


@pytest.mark.asyncio
async def test_supervisor_skips_duplicate_signal_at_intake() -> None:
    """If signals.status for the signal_id is non-pending (already mid-cascade
    from another supervisor or restart), the supervisor exits without doing
    anything (D-11)."""
    state_store = FakeStateStore()
    from signal_copier.infra.db_rows import SignalRow

    state_store.signals["test-sig-1"] = SignalRow(
        signal_id="test-sig-1",
        pair="EUR/JPY",
        broker_pair=None,
        broker_category=None,
        direction="down",
        trigger_hhmm="00:00",
        trigger_ts_unix=0.0,
        expiration_seconds=300,
        received_at_unix=0.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="(old)",
        status="placed_initial",
        error_reason=None,
        created_at_unix=0.0,
        updated_at_unix=0.0,
    )
    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        signal_id="test-sig-1",
    )

    await supervisor.run()

    # No notifier calls (no on_signal_received, no nothing).
    assert notifier.calls == []
    # No broker interactions.
    assert broker.place_calls == []


@pytest.mark.asyncio
async def test_supervisor_rejects_signal_when_daily_loss_limit_hit(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DAILY_LOSS_LIMIT > 0 and the day's realized_pnl <= -limit,
    the signal is marked error (daily_limit_hit) and broker.place() is
    not called."""
    # Build a Config with DAILY_LOSS_LIMIT=50, no Telegram creds needed.
    monkeypatch.setenv("DAILY_LOSS_LIMIT", "50.00")
    monkeypatch.setenv("DAILY_TRADE_LIMIT", "0")
    monkeypatch.setenv("DAILY_DRAWDOWN_PCT", "0")
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "log"))
    config = Config()

    state_store = FakeStateStore()
    today = make_signal_with_future_trigger(
        trigger_in_seconds=0.05,
        signal_id="test-sig-loss",
    ).received_at_unix
    from datetime import datetime

    today_date = datetime.fromtimestamp(today, tz=config.tz()).date()
    state_store.daily_summaries[today_date] = make_daily_summary(
        date_value=today_date,
        losses=10,
        trades_count=10,
        realized_pnl=Decimal("-60.00"),
    )

    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        config=config,
        signal_id="test-sig-loss",
    )

    await supervisor.run()

    # Broker was NOT called.
    assert broker.place_calls == []
    # Signal marked error with daily_limit_hit.
    assert any(
        u["new_state"] == "error" and u["error_reason"] == "daily_limit_hit"
        for u in state_store.state_updates
    )
    # Notification fired.
    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_rejected_by_limit" in method_names
    rejection_call = next(c for m, c in notifier.calls if m == "on_signal_rejected_by_limit")
    assert rejection_call["limit_type"] == "loss"


@pytest.mark.asyncio
async def test_supervisor_rejects_signal_when_daily_trade_limit_hit(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DAILY_TRADE_LIMIT > 0 and trades_count >= limit, reject."""
    monkeypatch.setenv("DAILY_LOSS_LIMIT", "0")
    monkeypatch.setenv("DAILY_TRADE_LIMIT", "5")
    monkeypatch.setenv("DAILY_DRAWDOWN_PCT", "0")
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "log"))
    config = Config()

    state_store = FakeStateStore()
    today = make_signal_with_future_trigger(
        trigger_in_seconds=0.05,
        signal_id="test-sig-count",
    ).received_at_unix
    from datetime import datetime

    today_date = datetime.fromtimestamp(today, tz=config.tz()).date()
    state_store.daily_summaries[today_date] = make_daily_summary(
        date_value=today_date,
        losses=2,
        trades_count=5,
        realized_pnl=Decimal("0.00"),
    )

    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        config=config,
        signal_id="test-sig-count",
    )

    await supervisor.run()

    assert broker.place_calls == []
    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_rejected_by_limit" in method_names
    rejection_call = next(c for m, c in notifier.calls if m == "on_signal_rejected_by_limit")
    assert rejection_call["limit_type"] == "count"


@pytest.mark.asyncio
async def test_supervisor_rejects_signal_when_daily_drawdown_limit_hit(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When DAILY_DRAWDOWN_PCT > 0 and realized_pnl <= -pct (USD threshold
    per M6 simplification), reject."""
    monkeypatch.setenv("DAILY_LOSS_LIMIT", "0")
    monkeypatch.setenv("DAILY_TRADE_LIMIT", "0")
    monkeypatch.setenv("DAILY_DRAWDOWN_PCT", "40")
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "log"))
    config = Config()

    state_store = FakeStateStore()
    today = make_signal_with_future_trigger(
        trigger_in_seconds=0.05,
        signal_id="test-sig-dd",
    ).received_at_unix
    from datetime import datetime

    today_date = datetime.fromtimestamp(today, tz=config.tz()).date()
    state_store.daily_summaries[today_date] = make_daily_summary(
        date_value=today_date,
        losses=5,
        trades_count=5,
        realized_pnl=Decimal("-50.00"),
    )

    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        config=config,
        signal_id="test-sig-dd",
    )

    await supervisor.run()

    assert broker.place_calls == []
    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_rejected_by_limit" in method_names
    rejection_call = next(c for m, c in notifier.calls if m == "on_signal_rejected_by_limit")
    assert rejection_call["limit_type"] == "drawdown"


@pytest.mark.asyncio
async def test_supervisor_no_rejection_when_limits_disabled(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default limits are 0 = disabled (FR-6.1/6.2/6.3). No rejection."""
    monkeypatch.setenv("DAILY_LOSS_LIMIT", "0")
    monkeypatch.setenv("DAILY_TRADE_LIMIT", "0")
    monkeypatch.setenv("DAILY_DRAWDOWN_PCT", "0")
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "log"))
    config = Config()

    state_store = FakeStateStore()
    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        config=config,
        signal_id="test-sig-nolimit",
    )
    supervisor._drive_cascade = _no_op_drive_cascade  # type: ignore[method-assign]

    await supervisor.run()

    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_rejected_by_limit" not in method_names
    assert "on_signal_received" in method_names


# --- _drive_cascade tests (Task 9) ----------------------------------------


@pytest.mark.asyncio
async def test_supervisor_initial_signal_expired_at_fire_time() -> None:
    """A signal whose trigger_unix_initial is already 5 seconds in the past
    causes compute_target_monotonic to return `loop.time()`. The FireEvent
    is dispatched immediately with now_unix >> trigger_unix, so the state
    machine transitions to error (signal_expired). No broker.place() call."""
    state_store = FakeStateStore()
    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        trigger_in_seconds=-5.0,  # already past
        signal_id="test-sig-expired",
    )

    await supervisor.run()

    # No broker interaction.
    assert broker.place_calls == []
    # Signal marked error with signal_expired.
    error_updates = [u for u in state_store.state_updates if u["new_state"] == "error"]
    assert len(error_updates) >= 1
    assert error_updates[-1]["error_reason"] == "signal_expired"
    # Notification fired: on_signal_expired + on_cascade_complete.
    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_expired" in method_names
    assert "on_cascade_complete" in method_names


@pytest.mark.asyncio
async def test_supervisor_initial_win_terminal() -> None:
    """Happy path: initial trigger fires, broker.place() returns trade_id,
    wait_result returns 'win', state machine → done_win, terminal."""
    state_store = FakeStateStore()
    supervisor, signal, broker, notifier = _make_supervisor(
        state_store=state_store,
        trigger_in_seconds=0.05,
        signal_id="test-sig-win",
    )
    # FakeBroker default_outcome='win' so wait_result returns 'win'.

    await supervisor.run()

    # Broker was called once for stage='initial' with amount=$2.
    assert len(broker.place_calls) == 1
    _, stage, amount = broker.place_calls[0]
    assert stage == "initial"
    assert amount == Decimal("2.00")
    # Stage row written.
    assert len(state_store.stages_placed) == 1
    assert state_store.stages_placed[0]["stage"] == "initial"
    # Stage result written.
    assert len(state_store.stage_results) == 1
    assert state_store.stage_results[0]["result"] == "win"
    # Final signal state: done_win.
    final_updates = [u for u in state_store.state_updates if u["new_state"] == "done_win"]
    assert len(final_updates) == 1
    # Notifications: on_signal_received, on_trade_placed, on_win, on_cascade_complete.
    method_names = [m for m, _ in notifier.calls]
    assert "on_signal_received" in method_names
    assert "on_trade_placed" in method_names
    assert "on_win" in method_names
    assert "on_cascade_complete" in method_names
