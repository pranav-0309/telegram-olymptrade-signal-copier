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
from typing import TYPE_CHECKING

from signal_copier.config import Config
from signal_copier.domain.signal import Signal
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
    """Per-signal cascade owner (stub for Tasks 7; full implementation in Tasks 9-14).

    This stub exists so the Scheduler class can import SignalSupervisor.
    Real behavior lands in subsequent tasks.
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
        """Stub. Real implementation lands in Tasks 9-14."""
        raise NotImplementedError("SignalSupervisor.run — see Tasks 9-14")
