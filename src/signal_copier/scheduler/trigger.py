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
from typing import TYPE_CHECKING

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage  # noqa: F401  (Stage used later)
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import (
    FireEvent,  # noqa: F401  (used in Task 9+)
    ResultEvent,  # noqa: F401  (used in Task 9+)
    SignalState,
    StageResult,  # noqa: F401  (used in Task 9+)
    TerminalState,  # noqa: F401  (used in Task 9+)
    transition,  # noqa: F401  (used in Task 9+)
)
from signal_copier.infra.clock import monotonic, now_unix
from signal_copier.notify.protocol import Notifier

if TYPE_CHECKING:
    from signal_copier.broker.base import Broker
    from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


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
        """Stub for now. Tasks 9-14 wire the full cascade loop."""
        raise NotImplementedError("_drive_cascade — see Tasks 9-14")

    async def _check_daily_limit(self) -> str | None:
        """Return 'loss' | 'count' | 'drawdown' if a daily limit is hit;
        None if all clear (FR-6.1/6.2/6.3). 0 = disabled (D-3).

        M6 simplification: `daily_drawdown_pct` is treated as a USD threshold
        (not a percentage of starting balance). M8 fixes the semantics
        when OlympTradeBroker.balance() is wired in at startup.
        """
        summary = await self._state_store.get_daily_summary(self._signal_date())
        if summary is None:
            return None

        cfg = self._config
        if cfg.daily_loss_limit > 0 and summary.realized_pnl <= -cfg.daily_loss_limit:
            return "loss"
        if cfg.daily_trade_limit > 0 and summary.trades_count >= cfg.daily_trade_limit:
            return "count"
        if cfg.daily_drawdown_pct > 0 and summary.realized_pnl <= -cfg.daily_drawdown_pct:
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
