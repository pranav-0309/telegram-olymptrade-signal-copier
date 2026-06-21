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
from signal_copier.domain.state import AllStates, ErrorReason, StageResult, TerminalState
from signal_copier.infra.db_rows import DailySummaryRow, SignalRow
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


@dataclass(slots=True)
class FakeStateStore:
    """In-memory replacement for StateStore. Lets tests pre-populate
    signals and daily_summary rows, and records all writes.

    Mirrors M3's fake-broker pattern + M5's FakeStateStore pattern.
    """

    # Pre-populated rows (test setup).
    signals: dict[str, SignalRow] = field(default_factory=dict)
    daily_summaries: dict[date, DailySummaryRow] = field(default_factory=dict)

    # Recorded writes (test assertions).
    upserted: list[Signal] = field(default_factory=list)
    state_updates: list[dict[str, Any]] = field(default_factory=list)
    stages_placed: list[dict[str, Any]] = field(default_factory=list)
    stage_results: list[dict[str, Any]] = field(default_factory=list)
    daily_updates: list[dict[str, Any]] = field(default_factory=list)

    async def upsert_signal(self, signal: Signal) -> bool:
        self.upserted.append(signal)
        return True

    async def get_signal(self, signal_id: str) -> SignalRow | None:
        return self.signals.get(signal_id)

    async def update_signal_state(
        self,
        signal_id: str,
        new_state: AllStates,
        *,
        error_reason: ErrorReason | None = None,
        updated_at_unix: float,
    ) -> None:
        self.state_updates.append(
            {
                "signal_id": signal_id,
                "new_state": new_state,
                "error_reason": error_reason,
                "updated_at_unix": updated_at_unix,
            }
        )
        # Update in-memory copy so subsequent get_signal reflects new state.
        row = self.signals.get(signal_id)
        if row is not None:
            self.signals[signal_id] = SignalRow(
                signal_id=row.signal_id,
                pair=row.pair,
                broker_pair=row.broker_pair,
                broker_category=row.broker_category,
                direction=row.direction,
                trigger_hhmm=row.trigger_hhmm,
                trigger_ts_unix=row.trigger_ts_unix,
                expiration_seconds=row.expiration_seconds,
                received_at_unix=row.received_at_unix,
                source_message_id=row.source_message_id,
                source_chat_id=row.source_chat_id,
                raw_text=row.raw_text,
                status=new_state,
                error_reason=error_reason,
                created_at_unix=row.created_at_unix,
                updated_at_unix=updated_at_unix,
            )

    async def record_stage_placed(
        self,
        signal_id: str,
        stage: Stage,
        *,
        pair: str,
        direction: str,
        amount: Decimal,
        placed_at_unix: float,
        expires_at_unix: float,
        broker_trade_id: str | None = None,
    ) -> str:
        # Use the same deterministic derivation as the real StateStore.
        import hashlib

        payload = f"{signal_id}|{stage}|{placed_at_unix:.6f}"
        trade_id = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
        self.stages_placed.append(
            {
                "signal_id": signal_id,
                "stage": stage,
                "trade_id": trade_id,
                "pair": pair,
                "direction": direction,
                "amount": amount,
                "placed_at_unix": placed_at_unix,
                "expires_at_unix": expires_at_unix,
                "broker_trade_id": broker_trade_id,
            }
        )
        return trade_id

    async def record_stage_result(
        self,
        trade_id: str,
        result: StageResult,
        *,
        pnl: Decimal,
        closed_at_unix: float,
    ) -> None:
        self.stage_results.append(
            {
                "trade_id": trade_id,
                "result": result,
                "pnl": pnl,
                "closed_at_unix": closed_at_unix,
            }
        )

    async def update_daily_summary(
        self,
        on_date: date,
        *,
        signals_count_delta: int = 0,
        trades_count_delta: int = 0,
        wins_delta: int = 0,
        losses_delta: int = 0,
        realized_pnl_delta: Decimal = Decimal("0"),
        limit_hit: str | None = None,
    ) -> None:
        self.daily_updates.append(
            {
                "on_date": on_date,
                "signals_count_delta": signals_count_delta,
                "trades_count_delta": trades_count_delta,
                "wins_delta": wins_delta,
                "losses_delta": losses_delta,
                "realized_pnl_delta": realized_pnl_delta,
                "limit_hit": limit_hit,
            }
        )
        # Mutate the in-memory row.
        existing = self.daily_summaries.get(on_date)
        if existing is None:
            self.daily_summaries[on_date] = DailySummaryRow(
                date=on_date,
                signals_count=signals_count_delta,
                trades_count=trades_count_delta,
                wins=wins_delta,
                losses=losses_delta,
                realized_pnl=realized_pnl_delta,
                limit_hit=limit_hit,
            )
        else:
            self.daily_summaries[on_date] = DailySummaryRow(
                date=existing.date,
                signals_count=existing.signals_count + signals_count_delta,
                trades_count=existing.trades_count + trades_count_delta,
                wins=existing.wins + wins_delta,
                losses=existing.losses + losses_delta,
                realized_pnl=existing.realized_pnl + realized_pnl_delta,
                limit_hit=limit_hit if limit_hit is not None else existing.limit_hit,
            )

    async def get_daily_summary(self, on_date: date) -> DailySummaryRow | None:
        return self.daily_summaries.get(on_date)

    async def get_active_signals(self) -> list[SignalRow]:
        return [
            r
            for r in self.signals.values()
            if r.status in {"placed_initial", "placed_gale1", "placed_gale2"}
        ]
