"""MT5-flavored reconnect primitives (M13.2).

Provides:
  - compute_backoff_seconds(attempt, base, cap, jitter) — exponential backoff
  - with_retry(op, *, op_name, on_retry, on_exhausted, max_attempts)
    — async function-call helper that retries `op()` on BrokerAuthError /
    OSError with exponential backoff. Notifies via the supplied callback
    hooks between attempts.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

from signal_copier.broker.base import BrokerAuthError


def compute_backoff_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.1,
) -> float:
    """Return `min(base * 2**attempt, cap)` with ±jitter randomization.

    `attempt` is 0-based. `jitter` is a fraction (0.1 = ±10%).
    """
    raw = base * (2**attempt)
    capped = min(raw, cap)
    if jitter == 0.0:
        return capped
    delta = capped * jitter * (random.random() * 2 - 1)  # noqa: S311 — not crypto
    return max(0.0, capped + delta)


async def with_retry(
    op: Callable[[], Awaitable[None]],
    *,
    op_name: str,
    on_retry: Callable[..., Awaitable[None]] | None = None,
    on_exhausted: Callable[..., Awaitable[None]] | None = None,
    max_attempts: int = 5,
) -> None:
    """Call `await op()` up to `max_attempts` times with exponential backoff.

    Retries on `BrokerAuthError` and `OSError` (the latter catches MT5
    IPC socket drops). Other exceptions are re-raised immediately.

    On each retry: optionally awaits `on_retry(attempt, max_attempts,
    downtime_seconds, next_delay_seconds)`. On final exhaustion:
    optionally awaits `on_exhausted(attempts, total_downtime_seconds)`
    then raises `BrokerAuthError(f"{op_name} failed after {max_attempts} attempts")`.
    """
    downtime_start = time.monotonic()
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            await op()
            return  # success
        except (BrokerAuthError, OSError) as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            delay = compute_backoff_seconds(attempt)
            if on_retry is not None:
                await on_retry(
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    downtime_seconds=time.monotonic() - downtime_start,
                    next_delay_seconds=delay,
                )
            await asyncio.sleep(delay)

    total_downtime = time.monotonic() - downtime_start
    if on_exhausted is not None:
        await on_exhausted(
            attempts=max_attempts,
            total_downtime_seconds=total_downtime,
        )
    raise BrokerAuthError(f"{op_name} failed after {max_attempts} attempts: {last_exc}")
