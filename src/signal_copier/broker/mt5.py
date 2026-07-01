"""MT5 broker implementation (M13.2).

Connects to MT5 via the `mt5linux` drop-in client. Implements the Broker
Protocol. Account-specific: all pairs end with `-STD` (VT Markets STD
demo). Lots hardcoded by stage (docs/refactor.md §1.3).

`mt5linux` is imported at module scope so tests can mock it via
`monkeypatch.setattr`. The real package is a thin drop-in for the
official `MetaTrader5` PyPI package — same API surface.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from enum import IntEnum
from typing import Any

import mt5linux as mt5

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

SYMBOL_SUFFIX: str = "-STD"  # VT Markets STD demo (per user account choice)

_POLL_INTERVAL_SEC: float = 0.25

# Pairs known at startup (pre-populated in `_load_symbol_cache` after connect).
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


# MT5 retcodes we recognize (subset of MQL5 TRADE_RETCODE_*).
class _Retcode(IntEnum):
    OK = 10009
    NO_MONEY = 10018
    REQUOTE = 10004
    PRICE_CHANGED = 10019
    REJECT = 10006
    INVALID_PRICE = 10003


# --- Helper functions (testable in isolation) ---


def _resolve_symbol_name(
    input_pair: str,
    *,
    symbol_info_fn,
    symbols_get_fn,
) -> str | None:
    """Translate `EUR/USD` → `EURUSD-STD` (or None).

    Tries the canonical suffixed name first, then falls back to a
    prefix-match via `symbols_get`. Pure function (callers inject the
    MT5 functions for testability).
    """
    base = input_pair.replace("/", "")
    target = base + SYMBOL_SUFFIX
    if symbol_info_fn(target) is not None:
        return target
    matches = symbols_get_fn(f"*{base}*") or []
    if not matches:
        return None
    for s in matches:
        if getattr(s, "name", None) == target:
            return s.name
    return getattr(matches[0], "name", None)


# --- Mt5Broker class ---


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
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._notifier = notifier
        self._connected = False
        self._symbol_cache: dict[str, str] = {}
        self._last_known_profit: dict[str, Decimal] = {}
        self._start_of_day_balance: Decimal | None = None

    # -- connect / close --

    async def connect(self) -> None:
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
        self._connected = True
        self._cache_start_of_day_balance()
        self._load_symbol_cache()
        on_reconnected = getattr(self._notifier, "on_broker_reconnected", None)
        if on_reconnected is not None:
            await on_reconnected(
                attempts_used=1,
                total_downtime_seconds=0.0,
            )

    def _sync_initialize(self) -> None:
        ok = mt5.initialize(
            path=self._terminal_path,
            server=self._server,
            login=self._login,
            password=self._password,
        )
        if not ok:
            err = mt5.last_error()
            _log.warning(
                "mt5.initialize failed: login=%s server=%s last_error=%s",
                self._login,
                self._server,
                err,
            )
            raise BrokerAuthError(f"mt5.initialize failed: {err}")

    def _cache_start_of_day_balance(self) -> None:
        info = mt5.account_info()
        if info is None:
            _log.warning(
                "mt5.account_info() returned None; daily_drawdown_pct falls back to USD threshold"
            )
            return
        balance = getattr(info, "balance", None)
        if balance is None:
            _log.warning("mt5.account_info().balance is None; daily_drawdown_pct falls back")
            return
        self._start_of_day_balance = Decimal(str(balance))

    def _load_symbol_cache(self) -> None:
        for input_pair in _KNOWN_INPUT_PAIRS:
            resolved = _resolve_symbol_name(
                input_pair,
                symbol_info_fn=mt5.symbol_info,
                symbols_get_fn=mt5.symbols_get,
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
        try:
            await asyncio.to_thread(mt5.shutdown)
        except Exception:
            _log.warning("mt5.shutdown raised; ignoring on teardown", exc_info=True)
        self._connected = False

    # -- place --

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,  # noqa: ARG002 — amount ignored, lots keyed on stage
    ) -> str:
        lots = LOTS_BY_STAGE[stage]
        broker_symbol = self._symbol_cache.get(signal.pair)
        if broker_symbol is None:
            broker_symbol = _resolve_symbol_name(
                signal.pair,
                symbol_info_fn=mt5.symbol_info,
                symbols_get_fn=mt5.symbols_get,
            )
        if broker_symbol is None:
            tried = signal.pair.replace("/", "") + SYMBOL_SUFFIX
            raise UnsupportedPairError(f"MT5 symbol not found for {signal.pair} (tried {tried})")

        direction = mt5.ORDER_TYPE_BUY if signal.direction == "up" else mt5.ORDER_TYPE_SELL

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(lots),
            "type": direction,
            "magic": 0,
            "comment": f"signal-copier:{signal.signal_id}:{stage}",
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = await mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            raise BrokerAuthError(f"mt5.order_send returned None: {err}")
        retcode = getattr(result, "retcode", 0)
        comment = getattr(result, "comment", "")
        if retcode != _Retcode.OK:
            _log.warning(
                "mt5.order_send non-OK: signal=%s stage=%s retcode=%s comment=%s",
                signal.signal_id,
                stage,
                retcode,
                comment,
            )
            if retcode in (_Retcode.NO_MONEY, _Retcode.PRICE_CHANGED):
                raise BrokerAuthError(
                    f"Insufficient funds for {stage}: retcode={retcode} comment={comment}"
                )
            if retcode == _Retcode.REJECT:
                raise UnsupportedPairError(
                    f"MT5 rejected order: retcode={retcode} comment={comment}"
                )
            raise BrokerAuthError(f"mt5.order_send failed: retcode={retcode} comment={comment}")

        ticket_value = getattr(result, "order", "")
        if not ticket_value:
            raise BrokerAuthError(
                f"mt5.order_send returned OK (retcode={retcode}) but no order id: comment={comment}"
            )
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
        async def _poll_for_close() -> StageResult:
            while True:
                positions = await asyncio.to_thread(mt5.positions_get, ticket=int(trade_id))
                if not positions:  # position gone
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

    # -- close_position --

    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> Decimal:
        def _close() -> Any:
            positions_before = mt5.positions_get(ticket=int(trade_id)) or []
            profit_before = Decimal("0")
            if positions_before:
                profit_before = Decimal(str(getattr(positions_before[0], "profit", 0)))
            result = mt5.Close(ticket=int(trade_id))
            retcode = getattr(result, "retcode", 0)
            comment = getattr(result, "comment", "")
            if retcode != _Retcode.OK:
                _log.warning(
                    "mt5.Close non-OK: trade_id=%s retcode=%s comment=%s",
                    trade_id,
                    retcode,
                    comment,
                )
            self._last_known_profit[trade_id] = profit_before
            return profit_before

        return await asyncio.wait_for(asyncio.to_thread(_close), timeout=timeout)
