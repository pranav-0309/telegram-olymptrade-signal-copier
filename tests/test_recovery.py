"""Unit tests for signal_copier.recovery."""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_copier.domain.gale import Stage
from signal_copier.infra.db_rows import SignalRow, StageRow
from signal_copier.recovery import (
    _STAGE_WINDOW_SECONDS,
    RecoveryReport,
    recover_active_signals,
)
from tests._scheduler_fixtures import FakeStateStore


class RecordingScheduler:
    """Captures adopt() and record_timeout() calls from recovery."""

    def __init__(self) -> None:
        self.adopted: list[tuple[str, str]] = []  # (signal_id, status)
        self.timed_out: list[tuple[str, str]] = []  # (signal_id, stage)

    async def adopt(self, signal_row: object) -> None:
        # signal_row is signal_copier.infra.db_rows.SignalRow
        # recovery passes the row through; we just record its id + status.
        self.adopted.append((signal_row.signal_id, signal_row.status))

    async def record_timeout(self, signal_id: str, stage: str) -> None:
        self.timed_out.append((signal_id, stage))


@pytest.mark.asyncio
async def test_recover_active_signals_returns_empty_report_when_no_active_signals() -> None:
    """No placed_* signals → report counts are all zero, no scheduler calls."""
    store = FakeStateStore()
    store.signals = {}  # nothing in placed_* states
    scheduler = RecordingScheduler()

    report = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),  # unused when no signals to recover
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=1_700_000_000.0,
    )

    assert isinstance(report, RecoveryReport)
    assert report.rehydrated == 0
    assert report.timed_out == 0
    assert report.abandoned == 0
    assert scheduler.adopted == []
    assert scheduler.timed_out == []


def _make_signal_row(
    *,
    signal_id: str = "sig-001",
    status: str = "placed_initial",
    trigger_ts_unix: float = 1_700_000_000.0,
    expiration_seconds: int = 300,
) -> SignalRow:
    return SignalRow(
        signal_id=signal_id,
        pair="EUR/JPY",
        broker_pair="EURJPY",
        broker_category="forex",
        direction="down",
        trigger_hhmm="10:20",
        trigger_ts_unix=trigger_ts_unix,
        expiration_seconds=expiration_seconds,
        received_at_unix=trigger_ts_unix - 60,
        source_message_id=1,
        source_chat_id=-100,
        raw_text="EUR/JPY;10:20;PUT🟥",
        status=status,  # type: ignore[arg-type]
        error_reason=None,
        created_at_unix=trigger_ts_unix - 60,
        updated_at_unix=trigger_ts_unix,
    )


def _make_stage_row(
    *,
    signal_id: str = "sig-001",
    stage: Stage = "initial",
    placed_at_unix: float,
) -> StageRow:
    return StageRow(
        trade_id=f"trade-{signal_id}-{stage}",
        signal_id=signal_id,
        stage=stage,
        pair="EUR/JPY",
        direction="down",
        amount=Decimal("2.00"),
        placed_at_unix=placed_at_unix,
        expires_at_unix=placed_at_unix + 300,
        closed_at_unix=None,
        pnl=None,
        result="open",
        broker_trade_id="broker-1",
    )


@pytest.mark.asyncio
async def test_recover_within_window_calls_adopt() -> None:
    """A placed_* signal whose stage window is still open → scheduler.adopt().

    Stage fired 10 seconds ago; expiration is 300s + 30s grace = window still
    open. Recovery should rehydrate (adopt), not time out.
    """
    stage_fire = 1_700_000_000.0
    now = stage_fire + 10.0  # 10 seconds after placement

    store = FakeStateStore()
    signal_row = _make_signal_row(status="placed_initial", trigger_ts_unix=stage_fire)
    store.signals[signal_row.signal_id] = signal_row
    # Patch FakeStateStore.get_active_signals to return this row.
    store.signals = {signal_row.signal_id: signal_row}

    scheduler = RecordingScheduler()

    report = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=now,
    )

    assert report.rehydrated == 1
    assert report.timed_out == 0
    assert scheduler.adopted == [("sig-001", "placed_initial")]
    assert scheduler.timed_out == []


def test_stage_window_seconds_constant_is_correct() -> None:
    """PRD FR-5.3: grace is 30s. Window = expiration + 30."""
    # 300s expiration + 30s grace = 330s window from stage fire time.
    assert _STAGE_WINDOW_SECONDS == 330


@pytest.mark.asyncio
async def test_recover_expired_window_calls_record_timeout() -> None:
    """A placed_* signal whose stage window has CLOSED → scheduler.record_timeout().

    Stage fired 600 seconds ago; window (expiration+grace=330s) is past.
    Recovery records timeout (state machine then advances or ends cascade).
    """
    stage_fire = 1_700_000_000.0
    now = stage_fire + 600.0  # 10 minutes after placement → window past

    store = FakeStateStore()
    signal_row = _make_signal_row(status="placed_initial", trigger_ts_unix=stage_fire)
    store.signals = {signal_row.signal_id: signal_row}

    scheduler = RecordingScheduler()

    report = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=now,
    )

    assert report.rehydrated == 0
    assert report.timed_out == 1
    assert scheduler.timed_out == [("sig-001", "initial")]
    assert scheduler.adopted == []


@pytest.mark.asyncio
async def test_recover_expired_gale2_window() -> None:
    """placed_gale2 with expired window → record_timeout(stage='gale2')."""
    trigger = 1_700_000_000.0
    gale2_fire = trigger + 600.0  # gale2 = trigger + 2*expiration
    now = gale2_fire + 600.0  # well past gale2's window

    store = FakeStateStore()
    signal_row = _make_signal_row(
        signal_id="sig-g2",
        status="placed_gale2",
        trigger_ts_unix=trigger,
    )
    store.signals = {signal_row.signal_id: signal_row}

    scheduler = RecordingScheduler()

    report = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=now,
    )

    assert report.timed_out == 1
    assert scheduler.timed_out == [("sig-g2", "gale2")]


@pytest.mark.asyncio
async def test_recover_idempotent_no_active_signals_returns_zero() -> None:
    """Calling recovery twice with no active signals → both reports zero, no scheduler calls.

    Proves the no-op short-circuit (line 54) is safe to re-run on every boot.
    """
    store = FakeStateStore()
    store.signals = {}
    scheduler = RecordingScheduler()

    report1 = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=1.0,
    )
    report2 = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=2.0,
    )

    assert report1.rehydrated == report2.rehydrated == 0
    assert report1.timed_out == report2.timed_out == 0
    assert scheduler.adopted == []
    assert scheduler.timed_out == []


@pytest.mark.asyncio
async def test_recover_mixed_signals_calls_correct_handlers_per_signal() -> None:
    """Mixed active signals: stale routes to record_timeout, fresh routes to adopt.

    sig-fresh (placed_initial, trigger=1_700_000_000) at now=+400s: window
    ended at +330s → timed_out. sig-stale (placed_gale1, same trigger) at +400s:
    gale1 stage_fire = +300s, window ends at +630s → still within window → adopt.
    """
    store = FakeStateStore()
    store.signals = {
        "sig-fresh": _make_signal_row(
            signal_id="sig-fresh",
            status="placed_initial",
            trigger_ts_unix=1_700_000_000.0,
        ),
        "sig-stale": _make_signal_row(
            signal_id="sig-stale",
            status="placed_gale1",
            trigger_ts_unix=1_700_000_000.0,
        ),
    }

    scheduler2 = RecordingScheduler()
    now_late = 1_700_000_400.0

    report2 = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),
        scheduler=scheduler2,  # type: ignore[arg-type]
        now_unix=now_late,
    )

    assert report2.rehydrated == 1
    assert report2.timed_out == 1
    assert ("sig-fresh", "initial") in scheduler2.timed_out
    assert ("sig-stale", "placed_gale1") in [a for a in scheduler2.adopted]
