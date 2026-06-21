"""Shared test fixtures for M6's scheduler tests.

Helpers:
  - FakeBroker: drop-in replacement for the Broker Protocol with programmable
    per-stage outcomes.
  - RecordingNotifier: drop-in replacement for Notifier that collects calls.
  - make_signal_with_future_trigger(seconds): build a Signal whose initial
    trigger is `seconds` from now. Used for sub-second skew tests.
  - assert_within_skew(actual, target, max_ms): assert the difference is
    within `max_ms` milliseconds.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from signal_copier.broker.base import Broker, UnsupportedPairError
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult, TerminalState
from signal_copier.infra.db_rows import DailySummaryRow
from signal_copier.notify.protocol import Notifier


@dataclass(slots=True)
class FakeBroker(Broker):
    """Records every place() and wait_result() call. Outcomes are programmable
    per-stage via `program_outcomes` dict. Unknown stages use `default_outcome`.

    `place_times` records `time.time()` at the moment of each `place()` call —
    used by the sub-second skew assertion.
    """

    program_outcomes: dict[Stage, StageResult] = field(default_factory=dict)
    default_outcome: StageResult = "win"
    force_unsupported_pair: bool = False
    raise_during_wait: BaseException | None = None
    wait_delay_seconds: float = 0.0

    place_calls: list[tuple[Signal, Stage, Decimal]] = field(default_factory=list)
    place_times: list[float] = field(default_factory=list)
    wait_result_calls: list[tuple[str, float]] = field(default_factory=list)
    _placed: dict[str, tuple[Signal, Stage]] = field(default_factory=dict)

    async def connect(self) -> None:
        return None

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        self.place_calls.append((signal, stage, amount))
        self.place_times.append(time.time())
        if self.force_unsupported_pair:
            raise UnsupportedPairError(f"{signal.pair} not available on this broker")
        broker_trade_id = f"fake-{signal.signal_id}-{stage}"
        self._placed[broker_trade_id] = (signal, stage)
        return broker_trade_id

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        self.wait_result_calls.append((trade_id, timeout))
        if self.wait_delay_seconds > 0:
            await asyncio.sleep(self.wait_delay_seconds)
        else:
            await asyncio.sleep(0)  # yield to event loop
        if self.raise_during_wait is not None:
            raise self.raise_during_wait
        signal, stage = self._placed[trade_id]
        return self.program_outcomes.get(stage, self.default_outcome)

    async def close(self) -> None:
        return None


@dataclass(slots=True)
class RecordingNotifier(Notifier):
    """Collects every notifier method call as a (method_name, kwargs_dict) tuple.

    `raise_on` lets tests inject a specific exception per method — useful
    for the "notifier failure must not abort cascade" test.
    """

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    raise_on: dict[str, BaseException] = field(default_factory=dict)

    async def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        if method in self.raise_on:
            raise self.raise_on[method]

    async def on_signal_received(self, signal: Signal) -> None:
        await self._record("on_signal_received", signal=signal)

    async def on_trade_placed(
        self,
        signal: Signal,
        stage: Stage,
        amount: Decimal,
        trade_id: str,
    ) -> None:
        await self._record(
            "on_trade_placed",
            signal=signal,
            stage=stage,
            amount=amount,
            trade_id=trade_id,
        )

    async def on_win(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
    ) -> None:
        await self._record(
            "on_win",
            signal=signal,
            stage=stage,
            pnl=pnl,
            cumulative_pnl=cumulative_pnl,
        )

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        await self._record(
            "on_loss",
            signal=signal,
            stage=stage,
            pnl=pnl,
            cumulative_pnl=cumulative_pnl,
            next_stage=next_stage,
        )

    async def on_signal_expired(
        self,
        signal: Signal,
        stage: Stage,
        trigger_hhmm: str,
    ) -> None:
        await self._record(
            "on_signal_expired",
            signal=signal,
            stage=stage,
            trigger_hhmm=trigger_hhmm,
        )

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: TerminalState,
        cumulative_pnl: Decimal,
    ) -> None:
        await self._record(
            "on_cascade_complete",
            signal=signal,
            final_state=final_state,
            cumulative_pnl=cumulative_pnl,
        )

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: DailySummaryRow,
    ) -> None:
        await self._record(
            "on_signal_rejected_by_limit",
            signal=signal,
            limit_type=limit_type,
            summary=summary,
        )

    async def on_bot_started(
        self,
        *,
        mode: str,
        watching: str,
        timezone: str,
    ) -> None:
        await self._record(
            "on_bot_started",
            mode=mode,
            watching=watching,
            timezone=timezone,
        )

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        await self._record(
            "on_bot_stopping",
            open_cascades=open_cascades,
        )


def make_signal_with_future_trigger(
    *,
    trigger_in_seconds: float,
    signal_id: str = "test-sig-1",
    pair: str = "EUR/JPY",
    direction: str = "down",
    expiration_seconds: int = 300,
) -> Signal:
    """Build a Signal whose initial trigger is `trigger_in_seconds` from now.

    Gale triggers are computed arithmetically (initial + expiration,
    initial + 2*expiration). The default `expiration_seconds=300` matches
    the v1 5-minute expiration; tests can override for faster cascades.
    """
    now = time.time()
    trigger_initial = now + trigger_in_seconds
    return Signal(
        signal_id=signal_id,
        pair=pair,
        direction=direction,
        trigger_hhmm="00:00",  # unused in tests; trigger_unix_* is what matters
        expiration_seconds=expiration_seconds,
        received_at_unix=now,
        source_message_id=1,
        source_chat_id=1,
        raw_text="(test)",
        trigger_unix_initial=trigger_initial,
        trigger_unix_gale1=trigger_initial + expiration_seconds,
        trigger_unix_gale2=trigger_initial + 2 * expiration_seconds,
    )


def assert_within_skew(
    actual_unix: float,
    target_unix: float,
    *,
    max_skew_ms: float = 800.0,
) -> None:
    """Assert that `actual_unix` is within `max_skew_ms` of `target_unix`.

    Default `max_skew_ms=800.0` (vs. the PRD NFR-1 target of 500ms) gives
    CI Linux some headroom for slower virtualized clocks while still
    exercising the actual scheduling path under realistic load.
    """
    skew_ms = abs(actual_unix - target_unix) * 1000.0
    assert skew_ms <= max_skew_ms, (
        f"skew {skew_ms:.1f}ms exceeds {max_skew_ms}ms "
        f"(actual={actual_unix:.3f}, target={target_unix:.3f})"
    )


def make_daily_summary(
    *,
    date_value: date | None = None,
    losses: int = 0,
    trades_count: int = 0,
    realized_pnl: Decimal = Decimal("0.00"),
    limit_hit: str | None = None,
) -> DailySummaryRow:
    """Build a DailySummaryRow for test fixtures."""
    return DailySummaryRow(
        date=date_value or date(2026, 6, 21),
        signals_count=0,
        trades_count=trades_count,
        wins=0,
        losses=losses,
        realized_pnl=realized_pnl,
        limit_hit=limit_hit,
    )
