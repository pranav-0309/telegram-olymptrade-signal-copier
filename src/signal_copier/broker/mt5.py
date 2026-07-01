"""MT5 broker implementation (M13.2).

Connects to MT5 via a `MetaTrader5` instance from the `mt5linux` package.
Implements the Broker Protocol. Account-specific: all pairs end with
`-STD` (VT Markets STD demo). Lots hardcoded by stage (docs/refactor.md §1.3).

`MetaTrader5` is instantiated LAZILY in `connect()` — not in `__init__` —
so creating a `Mt5Broker(...)` object succeeds even when no rpyc server
is running (only `connect()` raises). Every MT5 method is called on the
instance (e.g., `self._mt5.order_send(...)`).

The instance connection uses rpyc classic; shutdown via `mt5.shutdown()`.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from mt5linux import MetaTrader5

from signal_copier.broker.base import (
    BrokerAuthError,
    UnsupportedPairError,
)
from signal_copier.broker.reconnect import with_retry
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)

# --- Module-level constants ---

LOTS_BY_STAGE: dict[Stage, Decimal] = {
    "initial": Decimal("0.01"),
    "gale1": Decimal("0.02"),
    "gale2": Decimal("0.04"),
}

SYMBOL_SUFFIX: str = "-STD"

_POLL_INTERVAL_SEC: float = 0.25

# 10 known tradable pairs for cache pre-population on connect
_KNOWN_INPUT_PAIRS: tuple[str, ...] = (
    "EUR/JPY",
    "EUR/USD",
    "EUR/GBP",
    "GBP/USD",
    "GBP/JPY",
    "USD/JPY",
    "USD/CHF",
    "USD/CAD",
    "AUD/USD",
    "NZD/USD",
)

# MT5 retcodes we recognize (subset of mt5linux.MetaTrader5.* IntEnum).
# Used as plain int comparisons because IntEnum members compare equal to ints.
RES_S_OK = 10009  # TRADE_RETCODE_DONE
RES_E_NO_MONEY = 10018  # TRADE_RETCODE_NO_MONEY
RES_E_REJECT = 10006  # TRADE_RETCODE_REJECT
RES_E_PRICE_CHANGED = 10019  # TRADE_RETCODE_PRICE_CHANGED


# --- Pure helper (testable) ---

from collections.abc import Callable, Sequence  # noqa: E402  (kept near the helper for readability)


def _resolve_symbol_name(
    input_pair: str,
    *,
    symbol_info_fn: Callable[[str], object | None],
    symbols_get_fn: Callable[[str], Sequence[object]],
) -> str | None:
    """Translate `EUR/USD` → `EURUSD-STD` (or None). Tries suffixed name first
    then prefix-match via `symbols_get`. Pure function for testability."""
    base = input_pair.replace("/", "")
    target = base + SYMBOL_SUFFIX
    info = symbol_info_fn(target)
    if info is not None:
        return target
    matches: Sequence[object] = symbols_get_fn(f"*{base}*") or ()
    if not matches:
        return None
    for s in matches:
        if getattr(s, "name", None) == target:
            return s.name  # type: ignore[attr-defined,no-any-return]
    return getattr(matches[0], "name", None)


# --- The Mt5Broker class ---


class Mt5Broker:
    """Real MT5 broker. Satisfies Broker Protocol."""

    def __init__(
        self,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None,
        notifier: object,
    ) -> None:
        # Store connection config only — do NOT instantiate MetaTrader5 here.
        # The instance is created in connect() so that Mt5Broker(...) succeeds
        # even without a running MT5/rpyc server.
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._notifier = notifier
        self._mt5: MetaTrader5 | None = None
        self._symbol_cache: dict[str, str] = {}
        self._last_known_profit: dict[str, Decimal] = {}
        self._start_of_day_balance: Decimal | None = None

    # -- connect / close --

    async def connect(self) -> None:
        """Lazy MetaTrader5() + initialize (with retry).

        - First line: instantiate MetaTrader5 (raises ConnectionRefusedError if
          no rpyc server is running — we let it propagate; the scheduler can
          treat that as a transient broker-down condition).
        - Then wrap initialize in `with_retry()` so transient IPC failures
          (server boots late) retry with exponential backoff.
        """
        if self._mt5 is None:
            self._mt5 = MetaTrader5()

        async def _initialize() -> None:
            await asyncio.to_thread(self._sync_initialize)

        try:
            await with_retry(
                _initialize,
                op_name="mt5.initialize",
                on_retry=self._emit_reconnecting,
                on_exhausted=self._emit_reconnect_failed,
            )
        except BrokerAuthError:
            raise
        self._cache_start_of_day_balance()
        self._load_symbol_cache()
        on_reconnected = getattr(self._notifier, "on_broker_reconnected", None)
        if on_reconnected is not None:
            await on_reconnected(attempts_used=1, total_downtime_seconds=0.0)

    def _sync_initialize(self) -> None:
        assert self._mt5 is not None
        ok = self._mt5.initialize(
            path=self._terminal_path,
            server=self._server,
            login=self._login,
            password=self._password,
        )
        if not ok:
            err = self._mt5.last_error()
            _log.warning(
                "mt5.initialize failed: login=%s server=%s last_error=%s",
                self._login,
                self._server,
                err,
            )
            raise BrokerAuthError(f"mt5.initialize failed: {err}")

    def _cache_start_of_day_balance(self) -> None:
        assert self._mt5 is not None
        info = self._mt5.account_info()
        if info is None:
            _log.warning(
                "mt5.account_info() returned None; "
                "daily_drawdown_pct falls back to USD threshold"
            )
            return
        balance = getattr(info, "balance", None)
        if balance is None:
            _log.warning("mt5.account_info().balance is None; daily_drawdown_pct falls back")
            return
        self._start_of_day_balance = Decimal(str(balance))

    def _load_symbol_cache(self) -> None:
        assert self._mt5 is not None
        for input_pair in _KNOWN_INPUT_PAIRS:
            resolved = _resolve_symbol_name(
                input_pair,
                symbol_info_fn=self._mt5.symbol_info,
                symbols_get_fn=self._mt5.symbols_get,
            )
            if resolved is not None:
                self._symbol_cache[input_pair] = resolved

    async def _emit_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        on_reconnecting = getattr(self._notifier, "on_broker_reconnecting", None)
        if on_reconnecting is not None:
            await on_reconnecting(
                attempt=attempt,
                max_attempts=max_attempts,
                downtime_seconds=downtime_seconds,
                next_delay_seconds=next_delay_seconds,
            )

    async def _emit_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        on_exhausted = getattr(self._notifier, "on_broker_reconnect_failed", None)
        if on_exhausted is not None:
            await on_exhausted(
                attempts=attempts,
                total_downtime_seconds=total_downtime_seconds,
            )

    async def close(self) -> None:
        """mt5.shutdown idempotent + try/except safety.

        Tears down the MetaTrader5 session. Subsequent broker method calls
        will fail because `self._mt5` is set to None after shutdown.
        """
        if self._mt5 is None:
            return  # idempotent: already closed
        try:
            await asyncio.to_thread(self._mt5.shutdown)
        except Exception:
            _log.warning("mt5.shutdown raised; ignoring on teardown", exc_info=True)
        self._mt5 = None

    # -- place --

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,  # noqa: ARG002 — amount ignored, lots keyed on stage
    ) -> str:
        if self._mt5 is None:
            raise BrokerAuthError("Mt5Broker is not connected; call connect() first")
        lots = LOTS_BY_STAGE[stage]
        broker_symbol = self._symbol_cache.get(signal.pair)
        if broker_symbol is None:
            broker_symbol = _resolve_symbol_name(
                signal.pair,
                symbol_info_fn=self._mt5.symbol_info,
                symbols_get_fn=self._mt5.symbols_get,
            )
        if broker_symbol is None:
            tried = signal.pair.replace("/", "") + SYMBOL_SUFFIX
            raise UnsupportedPairError(f"MT5 symbol not found for {signal.pair} (tried {tried})")

        direction = (
            self._mt5.ORDER_TYPE_BUY if signal.direction == "up" else self._mt5.ORDER_TYPE_SELL
        )

        def _send() -> Any:
            assert self._mt5 is not None
            request = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": float(lots),
                "type": direction,
                "magic": 0,
                "comment": f"signal-copier:{signal.signal_id}:{stage}",
                "type_filling": self._mt5.ORDER_FILLING_IOC,
            }
            result = self._mt5.order_send(request)
            if result is None:
                err = self._mt5.last_error()
                raise BrokerAuthError(f"mt5.order_send returned None: {err}")
            return result

        result = await asyncio.to_thread(_send)
        retcode = getattr(result, "retcode", 0)
        comment = getattr(result, "comment", "")
        if retcode != RES_S_OK:
            _log.warning(
                "mt5.order_send non-OK: signal=%s stage=%s retcode=%s comment=%s",
                signal.signal_id,
                stage,
                retcode,
                comment,
            )
            if retcode in (RES_E_NO_MONEY, RES_E_PRICE_CHANGED):
                raise BrokerAuthError(
                    f"Insufficient funds for {stage}: retcode={retcode} comment={comment}"
                )
            if retcode == RES_E_REJECT:
                raise UnsupportedPairError(
                    f"MT5 rejected order: retcode={retcode} comment={comment}"
                )
            raise BrokerAuthError(f"mt5.order_send failed: retcode={retcode} comment={comment}")

        ticket_value = getattr(result, "order", "")
        if not ticket_value:
            raise BrokerAuthError(f"mt5.order_send returned OK but no order id: comment={comment}")
        ticket = str(ticket_value)
        self._last_known_profit[ticket] = Decimal("0")  # overwritten on close
        return ticket

    # -- wait_result --

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        if self._mt5 is None:
            raise BrokerAuthError("Mt5Broker is not connected; call connect() first")

        async def _poll_for_close() -> StageResult:
            assert self._mt5 is not None
            while True:
                positions = await asyncio.to_thread(self._mt5.positions_get, ticket=int(trade_id))
                if not positions:
                    profit = self._last_known_profit.get(trade_id, Decimal("0"))
                    if profit > 0:
                        return "win"
                    if profit < 0:
                        return "loss"
                    return "tie"
                await asyncio.sleep(_POLL_INTERVAL_SEC)

        try:
            return await asyncio.wait_for(_poll_for_close(), timeout=timeout)
        except TimeoutError:
            _log.warning(
                "Mt5Broker.wait_result timeout: trade_id=%s timeout=%.1fs",
                trade_id,
                timeout,
            )
            return "timeout"

    # -- close_position (opposite-direction order_send) --

    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> Decimal:
        """Close via opposite-direction order_send (mt5.Close does NOT exist).

        Step 1: read the live position to capture profit.
        Step 2: send an opposite-direction order_send with position=<ticket>
                reference. The MT5 server closes the position net.

        Returns the Decimal profit captured BEFORE the close.
        """
        if self._mt5 is None:
            raise BrokerAuthError("Mt5Broker is not connected; call connect() first")

        def _close() -> Decimal:
            assert self._mt5 is not None
            positions = self._mt5.positions_get(ticket=int(trade_id)) or []
            if not positions:
                _log.warning(
                    "close_position: no open position for ticket=%s; returning Decimal(0)",
                    trade_id,
                )
                return Decimal("0")
            position = positions[0]
            profit = Decimal(str(getattr(position, "profit", 0)))

            position_type = getattr(position, "type", 0)
            # POSITION_TYPE_BUY=0 → close with SELL; POSITION_TYPE_SELL=1 → close with BUY
            opposite_type = (
                self._mt5.ORDER_TYPE_SELL if position_type == 0 else self._mt5.ORDER_TYPE_BUY
            )
            request = {
                "action": self._mt5.TRADE_ACTION_DEAL,
                "symbol": getattr(position, "symbol", ""),
                "volume": float(getattr(position, "volume", 0)),
                "type": opposite_type,
                "position": int(trade_id),
                "magic": 0,
                "comment": "signal-copier:close",
                "type_filling": self._mt5.ORDER_FILLING_IOC,
            }
            result = self._mt5.order_send(request)
            retcode = getattr(result, "retcode", 0)
            comment = getattr(result, "comment", "")
            if retcode != RES_S_OK:
                _log.warning(
                    "close_position order_send non-OK: ticket=%s retcode=%s comment=%s",
                    trade_id,
                    retcode,
                    comment,
                )
            self._last_known_profit[trade_id] = profit
            return profit

        return await asyncio.wait_for(asyncio.to_thread(_close), timeout=timeout)
