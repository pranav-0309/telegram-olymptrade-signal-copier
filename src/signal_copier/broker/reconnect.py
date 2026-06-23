"""M10 — ReconnectingOlympTradeBroker.

Wraps OlympTradeBroker (broker/olymp.py) with a self-healing reconnect
supervisor. Detects WS drops via (a) a 1s polling watcher reading
inner._client.connection.is_connected, and (b) ConnectionError raised by
inner.place() / inner.wait_result(). On detection, runs an exponential-
backoff reconnect loop (1s → 2s → 4s → 8s → 16s → 30s cap, max
reconnect_max_attempts). After exhaustion, raises BrokerAuthError so
__main__ exits non-zero (Railway restart as backstop).

Spec: docs/superpowers/specs/2026-06-23-m10-olymptrade-reconnect-supervisor-design.md
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from olymptrade_ws import OlympTradeClient
from signal_copier.broker.base import BrokerAuthError
from signal_copier.broker.olymp import OlympTradeBroker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.infra.clock import monotonic
from signal_copier.notify.protocol import Notifier

if TYPE_CHECKING:
    from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)


_BACKOFF_BASE_SECONDS: float = 1.0
_BACKOFF_CAP_SECONDS: float = 30.0


class _ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


def compute_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a 30s cap. `attempt` is 0-indexed.

    attempt=0 -> 1.0, attempt=1 -> 2.0, attempt=4 -> 16.0, attempt>=5 -> 30.0.
    """
    return min(_BACKOFF_BASE_SECONDS * (2.0**attempt), _BACKOFF_CAP_SECONDS)


class ReconnectingOlympTradeBroker:
    """Wrapper around OlympTradeBroker with a self-healing reconnect loop.

    Satisfies the Broker Protocol (broker/base.py:40). Constructed with the
    same args as OlympTradeBroker plus two knobs: reconnect_max_attempts
    (default 5) and watcher_poll_seconds (default 1.0).

    The `connect()` method starts a background watcher task and calls
    `inner.connect()`. The watcher polls `inner._client.connection.is_connected`
    every `watcher_poll_seconds`. On a False reading (or on a ConnectionError
    from `place()`/`wait_result()`), it triggers `_trigger_reconnect()`,
    which tears down the dead inner broker, builds a fresh one via
    `_client_factory`, calls `connect()` on it, and atomically swaps the
    reference. After `reconnect_max_attempts` consecutive failures, raises
    BrokerAuthError so __main__ exits non-zero.

    In-flight cascades that hit a disconnect end with
    `error_reason='broker_unavailable'` via M6's existing ConnectionError→
    'error' mapping at scheduler/trigger.py:662 — the wrapper does not
    attempt to preserve or resume cascades across reconnect (M10 spec §2.2).
    """

    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        account_group: str = "demo",
        notifier: Notifier,
        _client_factory: Callable[[], OlympTradeClient] | None = None,
        reconnect_max_attempts: int = 5,
        watcher_poll_seconds: float = 1.0,
    ) -> None:
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client_factory = _client_factory or self._default_client_factory
        self._reconnect_max_attempts = reconnect_max_attempts
        self._watcher_poll_seconds = watcher_poll_seconds

        self._inner: OlympTradeBroker | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._reconnect_lock = asyncio.Lock()
        self._consecutive_failures: int = 0
        self._state: _ConnectionState = _ConnectionState.DISCONNECTED

    def _default_client_factory(self) -> OlympTradeClient:
        return OlympTradeClient(
            access_token=self._access_token,
            account_id=int(self._account_id) if self._account_id else None,  # type: ignore[arg-type]
            account_group=self._account_group,
            log_raw_messages=False,
        )

    def _build_inner(self) -> OlympTradeBroker:
        return OlympTradeBroker(
            access_token=self._access_token,
            account_id=self._account_id,
            account_group=self._account_group,
            notifier=self._notifier,
            _client_factory=self._client_factory,
        )

    async def connect(self) -> None:
        """Build inner broker, connect it, start watcher task."""
        self._inner = self._build_inner()
        await self._inner.connect()
        self._state = _ConnectionState.CONNECTED
        self._consecutive_failures = 0
        self._watcher = asyncio.create_task(self._watcher_loop(), name="olymp-watcher")

    async def place(self, signal: Signal, *, stage: Stage, amount: Decimal) -> str:
        """Delegate to inner; on ConnectionError trigger reconnect and re-raise."""
        assert self._inner is not None
        try:
            return await self._inner.place(signal, stage=stage, amount=amount)
        except ConnectionError:
            await self._trigger_reconnect()
            raise

    async def wait_result(self, trade_id: str, *, timeout: float) -> StageResult:
        """Delegate to inner; on ConnectionError trigger reconnect and re-raise."""
        assert self._inner is not None
        try:
            return await self._inner.wait_result(trade_id, timeout=timeout)
        except ConnectionError:
            await self._trigger_reconnect()
            raise

    async def close(self) -> None:
        """Cancel watcher task, close inner broker. Idempotent."""
        if self._watcher is not None and not self._watcher.done():
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
        self._watcher = None
        if self._inner is not None:
            await self._inner.close()
        self._state = _ConnectionState.DISCONNECTED

    async def _watcher_loop(self) -> None:
        """Poll is_connected every watcher_poll_seconds; trigger reconnect on False."""
        try:
            while True:
                await asyncio.sleep(self._watcher_poll_seconds)
                if self._state != _ConnectionState.CONNECTED:
                    continue
                if self._inner is None or self._inner._client is None:
                    continue
                if not self._inner._client.connection.is_connected:
                    await self._trigger_reconnect()
        except asyncio.CancelledError:
            return

    async def _trigger_reconnect(self) -> None:
        """Acquire reconnect lock; run reconnect loop or no-op if already running.

        If a reconnect is already in progress, the caller blocks until that
        reconnect finishes (success or exhaustion). Otherwise, the caller
        runs the reconnect loop itself. After this coroutine returns,
        `self._inner` is either the new live broker (success) or the state
        is DISCONNECTED (exhaustion).
        """
        if self._state == _ConnectionState.RECONNECTING:
            # Another coroutine is already reconnecting; serialize on its completion.
            async with self._reconnect_lock:
                pass
            return
        # Set state BEFORE acquiring the lock so concurrent callers see RECONNECTING
        # and take the fast path above (closes the race window).
        self._state = _ConnectionState.RECONNECTING
        async with self._reconnect_lock:
            await self._reconnect_loop()

    async def _safe_notify(self, coro: Awaitable[object]) -> None:
        """Await a notifier call; absorb exceptions so they don't break the loop."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — defensive isolation
            _log.warning("notifier raised, continuing: exc=%s", exc)

    async def _reconnect_loop(self) -> None:
        """Tear down dead inner; up to reconnect_max_attempts fresh connects.

        Sets self._state = RECONNECTING at entry; back to CONNECTED on success
        or DISCONNECTED on exhaustion. Notifies user at each lifecycle event.
        CancelledError (e.g. from close() during backoff) propagates; the
        caller is responsible for resetting state to DISCONNECTED.
        """
        self._state = _ConnectionState.RECONNECTING
        disconnect_detected_at = monotonic()
        await self._safe_notify(self._notifier.on_olymp_disconnect())

        if self._inner is not None:
            try:
                await self._inner.close()
            except Exception as exc:  # noqa: BLE001 — close is best-effort
                _log.warning("inner.close raised during reconnect: exc=%s", exc)

        last_exc: Exception | None = None
        for attempt in range(1, self._reconnect_max_attempts + 1):
            delay = compute_backoff_seconds(attempt - 1)
            downtime = monotonic() - disconnect_detected_at
            await self._safe_notify(
                self._notifier.on_olymp_reconnecting(
                    attempt=attempt,
                    max_attempts=self._reconnect_max_attempts,
                    downtime_seconds=downtime,
                    next_delay_seconds=delay,
                )
            )
            await asyncio.sleep(delay)
            try:
                new_inner = self._build_inner()
                await new_inner.connect()
            except Exception as exc:  # noqa: BLE001 — connection failure
                self._consecutive_failures += 1
                last_exc = exc
                _log.warning(
                    "reconnect attempt %d/%d failed: exc=%s",
                    attempt,
                    self._reconnect_max_attempts,
                    exc,
                )
                continue

            # Success: swap, notify, return.
            self._inner = new_inner
            self._consecutive_failures = 0
            self._state = _ConnectionState.CONNECTED
            total_downtime = monotonic() - disconnect_detected_at
            await self._safe_notify(
                self._notifier.on_olymp_reconnected(
                    attempts_used=attempt,
                    total_downtime_seconds=total_downtime,
                )
            )
            _log.info(
                "OlympTrade reconnected on attempt %d/%d after %.1fs",
                attempt,
                self._reconnect_max_attempts,
                total_downtime,
            )
            return

        # Exhausted.
        self._state = _ConnectionState.DISCONNECTED
        total_downtime = monotonic() - disconnect_detected_at
        await self._safe_notify(
            self._notifier.on_olymp_reconnect_failed(
                attempts=self._reconnect_max_attempts,
                total_downtime_seconds=total_downtime,
            )
        )
        raise BrokerAuthError(
            f"OlympTrade reconnect exhausted after {self._reconnect_max_attempts} attempts"
        ) from last_exc
