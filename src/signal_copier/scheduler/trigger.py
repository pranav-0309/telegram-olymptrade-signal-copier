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

from signal_copier.infra.clock import monotonic, now_unix


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
