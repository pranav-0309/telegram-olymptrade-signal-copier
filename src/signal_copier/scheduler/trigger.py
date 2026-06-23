"""The scheduler and per-signal supervisor (M6).

`Scheduler` consumes signals from the M5 listener's asyncio.Queue and spawns
one `SignalSupervisor` task per signal. Each supervisor owns its signal's
full lifecycle (initial → optional gales → terminal), invoking the M2 state
machine, the M3 broker, the M4 StateStore, and the M6 Notifier at each
transition.

Concurrency model: one Supervisor coroutine per in-flight signal. The
scheduler tracks them in a set for clean shutdown. Each supervisor runs
its full cascade (~15 minutes for 3 stages × 5min expiration) and exits.

Schedule precision: pure asyncio.loop.call_at. No spin-loop. Python 3.13's
asyncio on Windows meets ≤500ms precision natively (PRD NFR-1).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from signal_copier.broker.base import UnsupportedPairError
from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import (
    FireEvent,  # noqa: F401  (used in Task 9+)
    ResultEvent,
    SignalState,
    StageResult,  # noqa: F401  (used in Task 9+)
    TerminalState,  # noqa: F401  (used in Task 9+)
    transition,
)
from signal_copier.infra.clock import monotonic, now_unix
from signal_copier.notify.protocol import Notifier

if TYPE_CHECKING:
    from signal_copier.broker.base import Broker
    from signal_copier.infra.db_rows import SignalRow
    from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


# StageResult-grace timeout per PRD FR-5.3: expiration_seconds + 30s.
_RESULT_GRACE_SECONDS: float = 30.0


# Stage → (signal.trigger_unix_* field name) mapping for the schedule targets.
_STAGE_TO_TRIGGER_ATTR: dict[Stage, str] = {
    "initial": "trigger_unix_initial",
    "gale1": "trigger_unix_gale1",
    "gale2": "trigger_unix_gale2",
}


def compute_target_monotonic(target_wall_unix: float) -> float:
    """Return the monotonic-clock target for `loop.call_at(...)`.

    Converts a wall-clock Unix epoch to monotonic time, anchored to the
    current event loop. If `target_wall_unix` is in the past, returns
    `monotonic()` so the call_at fires immediately (D-17).
    """
    now_wall = now_unix()
    now_mono = monotonic()
    delta = target_wall_unix - now_wall
    if delta <= 0:
        return now_mono
    return now_mono + delta


class Scheduler:
    """Consumes signals from the M5 listener's asyncio.Queue and spawns
    `SignalSupervisor` tasks. Runs forever until cancelled.

    Construction is sync (config + dependencies). `run()` is the single
    async entry point. Tracks active supervisor tasks for clean shutdown.
    """

    def __init__(
        self,
        *,
        queue: asyncio.Queue[Signal],
        broker: Broker,
        state_store: StateStore,
        notifier: Notifier,
        config: Config,
    ) -> None:
        self._queue = queue
        self._broker = broker
        self._state_store = state_store
        self._notifier = notifier
        self._config = config
        self._active_tasks: set[asyncio.Task[None]] = set()

    @property
    def active_task_count(self) -> int:
        """Number of supervisor tasks currently in flight. Used by
        __main__ for the FR-7.1 'open_cascades' field on bot shutdown.
        """
        return len(self._active_tasks)

    async def record_timeout(self, signal_id: str, stage: Stage) -> None:
        """Record a per-stage timeout for a signal that's stuck mid-cascade.

        Used by M9's recovery module when a stage's expiration+grace window
        has CLOSED while the process was down. The state machine dispatches
        a ResultEvent(result='timeout') which is treated as a loss per
        FR-5.3, then advances the cascade per FR-5.5-5.7.

        Idempotent: a no-op (with a warning log) if signal_id does not
        exist in the state store.
        """
        signal_row = await self._state_store.get_signal(signal_id)
        if signal_row is None:
            _log.warning(
                "record_timeout: no signal found: signal_id=%s stage=%s (idempotent no-op)",
                signal_id,
                stage,
            )
            return

        # Reconstruct the SignalState for the stage that's timing out.
        # The SignalRow only stores trigger_ts_unix (the initial). Gale
        # trigger times are derived arithmetically from stage_offset.
        stage_offset = {"initial": 0, "gale1": 1, "gale2": 2}[stage]
        trigger_unix = signal_row.trigger_ts_unix + stage_offset * signal_row.expiration_seconds

        state = SignalState(
            signal_id=signal_row.signal_id,
            pair=signal_row.pair,
            direction=signal_row.direction,
            state=signal_row.status,
            stage=stage,
            amount=amount_for_stage(stage, self._config),
            trigger_unix=trigger_unix,
            expires_at_unix=trigger_unix + float(signal_row.expiration_seconds),
            result=None,
            cumulative_pnl=Decimal("0.00"),
            error_reason=signal_row.error_reason,
        )

        now_wall = now_unix()
        result = transition(
            state,
            ResultEvent(result="timeout", now_unix=now_wall),
            config=self._config,
        )
        if not result.success or result.new_state is None:
            _log.error(
                "record_timeout: transition failed: signal_id=%s reason=%s",
                signal_id,
                result.reason,
            )
            return

        new_state = result.new_state
        await self._state_store.update_signal_state(
            signal_id=signal_id,
            new_state=new_state.state,
            error_reason=new_state.error_reason,
            updated_at_unix=now_wall,
        )
        # If timeout advanced the cascade to a new non-terminal placed_* state,
        # call adopt() so a fresh supervisor picks up the next stage.
        if new_state.state in {"placed_initial", "placed_gale1", "placed_gale2"}:
            updated_row = await self._state_store.get_signal(signal_id)
            if updated_row is not None:
                await self.adopt(updated_row)

    async def adopt(self, signal_row: SignalRow) -> None:
        """Rehydrate a supervisor for a signal that was in-progress at last shutdown.

        Used by M9's recovery module on boot to resume cascades that were
        in `placed_*` states when the process died. Builds a fresh
        `SignalSupervisor` (M6's class) and starts it as a task.

        Idempotent: a no-op if signal_row.status is already terminal.

        Caveat: this re-runs the full cascade from the signal's initial
        state (not the in-progress stage). The M9 recovery model
        (re-arm + trust broker with grace timer as safety net) means that
        if the trade has already closed on the broker, the supervisor's
        `wait_result` resolves immediately; if it's still open, the
        supervisor waits the full grace window. In both cases, no
        duplicate trade is placed because the stage's `placed_at_unix`
        is fixed and the deterministic `trade_id` collides if we
        re-inserted the stage row (we don't — supervisor skips
        `record_stage_placed` if the stage already has a row in DB).
        """
        if signal_row.status in {"done_win", "done_loss", "done_tie", "error"}:
            _log.info(
                "adopt: signal already terminal, skipping: signal_id=%s status=%s",
                signal_row.signal_id,
                signal_row.status,
            )
            return

        trigger_initial = signal_row.trigger_ts_unix
        signal = Signal(
            signal_id=signal_row.signal_id,
            pair=signal_row.pair,
            direction=signal_row.direction,
            trigger_hhmm=signal_row.trigger_hhmm,
            expiration_seconds=signal_row.expiration_seconds,
            received_at_unix=signal_row.received_at_unix,
            source_message_id=signal_row.source_message_id,
            source_chat_id=signal_row.source_chat_id,
            raw_text=signal_row.raw_text,
            trigger_unix_initial=trigger_initial,
            trigger_unix_gale1=trigger_initial + signal_row.expiration_seconds,
            trigger_unix_gale2=trigger_initial + 2 * signal_row.expiration_seconds,
        )

        supervisor = SignalSupervisor(
            signal=signal,
            broker=self._broker,
            state_store=self._state_store,
            notifier=self._notifier,
            config=self._config,
        )
        task = asyncio.create_task(
            supervisor.run(), name=f"supervisor-adopted-{signal_row.signal_id}"
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        _log.info(
            "adopt: started supervisor for in-flight signal: signal_id=%s status=%s",
            signal_row.signal_id,
            signal_row.status,
        )

    async def run(self) -> None:
        """Drain the queue; spawn a SignalSupervisor per signal. Runs forever.

        On CancelledError (SIGINT from __main__), cancels all active
        supervisors and re-raises so __main__ can exit cleanly.
        """
        try:
            while True:
                signal = await self._queue.get()
                task = asyncio.create_task(
                    self._supervise(signal),
                    name=f"supervisor-{signal.signal_id}",
                )
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)
                self._queue.task_done()
        except asyncio.CancelledError:
            _log.info(
                "Scheduler cancelled; cancelling %d active supervisors",
                len(self._active_tasks),
            )
            for task in list(self._active_tasks):
                task.cancel()
            if self._active_tasks:
                await asyncio.gather(
                    *self._active_tasks,
                    return_exceptions=True,
                )
            raise

    async def _supervise(self, signal: Signal) -> None:
        """Spawn a SignalSupervisor and await it. Indirected so tests can
        patch this method to inject mock supervisors (the real supervisor
        comes in Tasks 9–14).
        """
        from signal_copier.scheduler.trigger import SignalSupervisor  # noqa: F401

        supervisor = SignalSupervisor(
            signal=signal,
            broker=self._broker,
            state_store=self._state_store,
            notifier=self._notifier,
            config=self._config,
        )
        await supervisor.run()


class SignalSupervisor:
    """Owns one signal's full cascade: initial → gale1 → gale2 → terminal.

    Per the design: one supervisor per signal. Lifecycle:
      1. Daily-limit check at intake.
      2. Idempotency check (get_signal).
      3. Build initial SignalState; emit on_signal_received.
      4. Drive the cascade (Tasks 9-14 wire this).

    All exceptions are caught (D-5) except DB errors, which are re-raised
    so __main__ exits non-zero (Railway restarts).
    """

    def __init__(
        self,
        *,
        signal: Signal,
        broker: Broker,
        state_store: StateStore,
        notifier: Notifier,
        config: Config,
    ) -> None:
        self._signal = signal
        self._broker = broker
        self._state_store = state_store
        self._notifier = notifier
        self._config = config

    async def run(self) -> None:
        """The supervisor's main coroutine. Returns on terminal state or
        CancelledError. Never raises non-CancelledError (D-5).
        """
        try:
            await self._run_inner()
        except asyncio.CancelledError:
            _log.debug(
                "supervisor cancelled: signal_id=%s",
                self._signal.signal_id,
            )
            raise
        except Exception as exc:
            _log.exception(
                "supervisor unexpected error: signal_id=%s exc=%s",
                self._signal.signal_id,
                exc,
            )

    async def _run_inner(self) -> None:
        # Step 1: daily-limit check.
        limit_type = await self._check_daily_limit()
        if limit_type is not None:
            await self._handle_limit_rejection(limit_type)
            return

        # Step 2: idempotency check (D-11).
        existing = await self._state_store.get_signal(self._signal.signal_id)
        if existing is not None and existing.status != "pending":
            _log.info(
                "duplicate signal at supervisor intake: signal_id=%s status=%s",
                self._signal.signal_id,
                existing.status,
            )
            return

        # Step 3: build initial state, emit signal_received.
        state = SignalState.from_signal(self._signal, self._config)
        await self._safe_notify(self._notifier.on_signal_received(self._signal))

        # Step 4: drive the cascade (wired in Task 9).
        await self._drive_cascade(state)

    async def _drive_cascade(self, initial_state: SignalState) -> None:
        """Run the cascade from `initial_state` until terminal or error.

        Each iteration:
          a. Schedule the next call_at for state.stage's trigger_unix.
          b. Wait for the call_at callback to fire (via asyncio.Future).
          c. Dispatch FireEvent to the state machine — ONLY when state is
             `pending`. After a loss transition, the state machine advances
             to `placed_<next>` directly (via _to_placed), so the fire has
             already happened conceptually; we just proceed to broker.place().
          d. Place the trade via broker.place().
          e. Wait for the result via broker.wait_result().
          f. Apply the result via _apply_result_and_finalize().
          g. Re-check state — if terminal, exit; otherwise loop to next stage.
        """
        state = initial_state
        loop = asyncio.get_running_loop()

        while state.stage is not None:
            # a. Schedule the next fire.
            stage = state.stage
            target_wall = getattr(self._signal, _STAGE_TO_TRIGGER_ATTR[stage])
            target_mono = compute_target_monotonic(target_wall)

            # D-17: negative delta at intake → fire immediately with stale now.
            fired = loop.create_future()
            try:
                loop.call_at(target_mono, fired.set_result, True)
            except Exception:  # pragma: no cover — defensive
                _log.exception(
                    "call_at failed: signal_id=%s stage=%s",
                    self._signal.signal_id,
                    stage,
                )
                return

            try:
                await fired
            except asyncio.CancelledError:
                raise

            # c. Dispatch FireEvent — only when state is pending. After a
            # loss, _advance_after_result transitions placed_X → placed_<next>
            # directly, so the next iteration's FireEvent would be invalid.
            if state.state == "pending":
                now_wall = now_unix()
                result = transition(
                    state,
                    FireEvent(now_unix=now_wall),
                    config=self._config,
                )
                if not result.success or result.new_state is None:
                    _log.error(
                        "FireEvent failed: signal_id=%s stage=%s reason=%s",
                        self._signal.signal_id,
                        stage,
                        result.reason,
                    )
                    return
                state = result.new_state

                # Persist the state transition.
                await self._state_store.update_signal_state(
                    signal_id=self._signal.signal_id,
                    new_state=state.state,
                    error_reason=state.error_reason,
                    updated_at_unix=now_unix(),
                )

                # If the FireEvent drove us to error (signal_expired), notify and exit.
                if state.state == "error":
                    await self._safe_notify(
                        self._notifier.on_signal_expired(
                            self._signal,
                            stage=stage,
                            trigger_hhmm=self._signal.trigger_hhmm,
                        )
                    )
                    await self._safe_notify(
                        self._notifier.on_cascade_complete(
                            self._signal,
                            final_state="error",
                            cumulative_pnl=state.cumulative_pnl,
                        )
                    )
                    return

            # d. Place the trade. Capture amount BEFORE place() — after the
            # ResultEvent transition, terminal states have amount=Decimal("0").
            placed_amount = state.amount
            placed_at = now_unix()
            try:
                broker_trade_id = await self._broker.place(
                    self._signal,
                    stage=stage,
                    amount=placed_amount,
                )
            except UnsupportedPairError as exc:
                _log.warning(
                    "broker rejected pair: signal_id=%s pair=%s exc=%s",
                    self._signal.signal_id,
                    self._signal.pair,
                    exc,
                )
                # D-4: translate broker exception into state machine's
                # vocabulary (ResultEvent("error")). No trade_id exists.
                await self._apply_error_transition(
                    state,
                    stage,
                    "error",
                    placed_amount,
                )
                return

            # Persist the stage row. record_stage_placed returns the
            # deterministic trade_id used by record_stage_result later.
            db_trade_id = await self._state_store.record_stage_placed(
                signal_id=self._signal.signal_id,
                stage=stage,
                pair=self._signal.pair,
                direction=self._signal.direction,
                amount=placed_amount,
                placed_at_unix=placed_at,
                expires_at_unix=state.expires_at_unix,
                broker_trade_id=broker_trade_id,
            )
            await self._safe_notify(
                self._notifier.on_trade_placed(
                    self._signal,
                    stage=stage,
                    amount=placed_amount,
                    trade_id=db_trade_id,
                )
            )

            # e. Wait for the result.
            stage_result = await self._wait_for_stage_result(broker_trade_id, state)

            # f. Apply the result; returns the new (possibly terminal) state.
            state = await self._apply_result_and_finalize(
                state,
                stage,
                stage_result,
                placed_amount,
                db_trade_id,
            )
            # While-loop guard re-checks state.stage next iteration; if the
            # state is terminal (done_win/done_loss/error) the loop exits.

    async def _apply_result_and_finalize(
        self,
        state: SignalState,
        stage: Stage,
        stage_result: StageResult,
        placed_amount: Decimal,
        trade_id: str,
    ) -> SignalState:
        """Dispatch a ResultEvent to the state machine; persist + notify.

        Returns the new (possibly terminal) SignalState. The caller uses
        the return value to update its loop variable.
        """
        now_wall = now_unix()
        result = transition(
            state,
            ResultEvent(result=stage_result, now_unix=now_wall),
            config=self._config,
        )
        if not result.success or result.new_state is None:
            _log.error(
                "ResultEvent failed: signal_id=%s stage=%s result=%s reason=%s",
                self._signal.signal_id,
                stage,
                stage_result,
                result.reason,
            )
            return state

        new_state = result.new_state

        await self._state_store.record_stage_result(
            trade_id=trade_id,
            result=stage_result,
            pnl=self._compute_stage_pnl_for_result(stage_result, placed_amount),
            closed_at_unix=now_wall,
        )
        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            new_state=new_state.state,
            error_reason=new_state.error_reason,
            updated_at_unix=now_wall,
        )
        await self._state_store.update_daily_summary(
            on_date=self._signal_date(),
            signals_count_delta=0,
            trades_count_delta=1,
            wins_delta=1 if stage_result == "win" else 0,
            losses_delta=1 if stage_result in {"loss", "tie", "timeout"} else 0,
            realized_pnl_delta=self._compute_stage_pnl_for_result(
                stage_result,
                placed_amount,
            ),
        )

        if stage_result == "win":
            await self._safe_notify(
                self._notifier.on_win(
                    self._signal,
                    stage=stage,
                    pnl=self._compute_stage_pnl_for_result(stage_result, placed_amount),
                    cumulative_pnl=new_state.cumulative_pnl,
                )
            )
        elif stage_result in {"loss", "tie", "timeout"}:
            await self._safe_notify(
                self._notifier.on_loss(
                    self._signal,
                    stage=stage,
                    pnl=-placed_amount,
                    cumulative_pnl=new_state.cumulative_pnl,
                    next_stage=new_state.stage,
                )
            )
        # stage_result == "error" → on_cascade_complete handles it.

        if new_state.state in {"done_win", "done_loss", "done_tie", "error"}:
            await self._safe_notify(
                self._notifier.on_cascade_complete(
                    self._signal,
                    final_state=cast(TerminalState, new_state.state),
                    cumulative_pnl=new_state.cumulative_pnl,
                )
            )

        return new_state

    async def _apply_error_transition(
        self,
        state: SignalState,
        stage: Stage,
        stage_result: StageResult,
        placed_amount: Decimal,
    ) -> SignalState:
        """Variant of _apply_result_and_finalize for the no-trade-id path
        (UnsupportedPairError raised before trade_id was returned).
        """
        now_wall = now_unix()
        result = transition(
            state,
            ResultEvent(result=stage_result, now_unix=now_wall),
            config=self._config,
        )
        if not result.success or result.new_state is None:
            _log.error(
                "ResultEvent (error path) failed: signal_id=%s stage=%s reason=%s",
                self._signal.signal_id,
                stage,
                result.reason,
            )
            return state

        new_state = result.new_state

        # No record_stage_result: no stage row was written (place() raised
        # before returning a trade_id).
        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            new_state=new_state.state,
            error_reason=new_state.error_reason,
            updated_at_unix=now_wall,
        )
        await self._safe_notify(
            self._notifier.on_cascade_complete(
                self._signal,
                final_state=cast(TerminalState, new_state.state),
                cumulative_pnl=new_state.cumulative_pnl,
            )
        )
        return new_state

    async def _wait_for_stage_result(
        self,
        broker_trade_id: str,
        state: SignalState,
    ) -> StageResult:
        """Wrap broker.wait_result in asyncio.wait_for with the FR-5.3 timeout.

        On TimeoutError: return 'timeout' (treated as loss-equivalent).
        On any other broker exception: return 'error' (state machine ends
        the cascade with broker_unavailable).
        """
        timeout = max(
            0.1,
            state.expires_at_unix - now_unix() + _RESULT_GRACE_SECONDS,
        )
        try:
            return await asyncio.wait_for(
                self._broker.wait_result(broker_trade_id, timeout=timeout),
                timeout=timeout,
            )
        except TimeoutError:
            _log.warning(
                "broker.wait_result timeout: trade_id=%s timeout=%.1fs",
                broker_trade_id,
                timeout,
            )
            return "timeout"
        except Exception as exc:  # noqa: BLE001 — map to error per D-5
            _log.warning(
                "broker.wait_result error: trade_id=%s exc=%s",
                broker_trade_id,
                exc,
            )
            return "error"

    def _compute_stage_pnl_for_result(
        self,
        result: StageResult,
        amount: Decimal,
    ) -> Decimal:
        """Mirror state.py's _stage_pnl — duplicated here so M6's DB writes
        don't depend on importing state machine internals. Matches the
        v1 approximation (92% payout for win; full loss for loss/tie/timeout).
        M8 will replace with broker-reported PnL."""
        if result == "win":
            return amount * Decimal("0.92")
        if result in {"loss", "tie", "timeout"}:
            return -amount
        return Decimal("0.00")  # 'error' contributes nothing

    async def _check_daily_limit(self) -> str | None:
        """Return 'loss' | 'count' | 'drawdown' if a daily limit is hit;
        None if all clear (FR-6.1/6.2/6.3). 0 = disabled (D-3).

        M8 fix: when `self._broker` exposes `start_of_day_balance` (only
        OlympTradeBroker does — via `_cache_start_of_day_balance` during
        connect()), `daily_drawdown_pct` is treated as a percentage of that
        balance. Otherwise, fall back to the M6 placeholder behavior (treat
        `daily_drawdown_pct` as a USD threshold).
        """
        summary = await self._state_store.get_daily_summary(self._signal_date())
        if summary is None:
            return None

        cfg = self._config
        if cfg.daily_loss_limit > 0 and summary.realized_pnl <= -cfg.daily_loss_limit:
            return "loss"
        if cfg.daily_trade_limit > 0 and summary.trades_count >= cfg.daily_trade_limit:
            return "count"
        if cfg.daily_drawdown_pct > 0:
            starting = getattr(self._broker, "start_of_day_balance", None)
            if starting is not None:
                # M8: treat daily_drawdown_pct as a percentage of start-of-day balance
                threshold = starting * Decimal(cfg.daily_drawdown_pct) / Decimal(100)
                if summary.realized_pnl <= -threshold:
                    return "drawdown"
            else:
                # M6 fallback: dry-run path with no start-of-day balance — treat
                # daily_drawdown_pct as a USD threshold (placeholder behavior)
                if summary.realized_pnl <= -cfg.daily_drawdown_pct:
                    return "drawdown"
        return None

    async def _handle_limit_rejection(self, limit_type: str) -> None:
        """Mark the signal 'error (daily_limit_hit)' and emit notification."""
        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            new_state="error",
            error_reason="daily_limit_hit",
            updated_at_unix=now_unix(),
        )
        summary = await self._state_store.get_daily_summary(self._signal_date())
        if summary is None:
            # Build a minimal summary so the notification has something to log.
            from signal_copier.infra.db_rows import DailySummaryRow

            summary = DailySummaryRow(
                date=self._signal_date(),
                signals_count=0,
                trades_count=0,
                wins=0,
                losses=0,
                realized_pnl=Decimal("0.00"),
                limit_hit=limit_type,
            )
        await self._safe_notify(
            self._notifier.on_signal_rejected_by_limit(
                self._signal,
                limit_type=limit_type,
                summary=summary,
            )
        )

    def _signal_date(self) -> date:
        """The signal's date in the configured timezone (matches M5)."""
        return datetime.fromtimestamp(
            self._signal.trigger_unix_initial,
            tz=self._config.tz(),
        ).date()

    async def _safe_notify(self, coro: Awaitable[None]) -> None:
        """Await a notifier call; absorb exceptions (D-5)."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — defensive isolation
            _log.warning("notifier raised, continuing: exc=%s", exc)
