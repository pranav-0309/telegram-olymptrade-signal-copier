"""OlympTradeBroker — concrete Broker implementation wrapping the vendored
olymptrade_ws client. Implements the M3 Broker Protocol with real I/O for
end-to-end demo trading.

Architecture (3 sub-components in one class):
  1. Asset-map cache (_build_asset_map) — built once at connect() from the
     e:1068 push that arrives during initialize_session().
  2. Push-event router (_on_trade_closed/accepted/interim) — registered as
     persistent callbacks on the vendored client at connect().
  3. Trade-result surface (place/wait_result) — per-trade Future keyed by
     broker trade_id; the e:26 callback resolves the matching Future.

Vendored library contract:
  - Imports use `from olymptrade_ws import OlympTradeClient, BalanceAPI,
    MarketAPI, TradeAPI` (see src/olymptrade_ws/__init__.py re-exports).
  - Event codes use `olymptrade_ws.olympconfig.parameters.E_*` constants.
  - NO edits to files under src/olymptrade_ws/ — this is vendored code
    per PRD R-15 / §12.6.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Literal, cast

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters
from signal_copier.broker.base import (
    BrokerAuthError,
    UnsupportedPairError,
)
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult
from signal_copier.notify.protocol import Notifier

_log = logging.getLogger(__name__)


# Event code for the e:1068 asset-list push (per spec §5.3; not in
# olympconfig.parameters constants as a named constant).
ASSET_LIST_EVENT: int = 1068

# Event code for the e:1054 instrument-list push — the reliable fallback
# for the asset map. e:1068 is marked "GUESS!" in the vendored library and
# sometimes never responds (account-scope dependent). e:1054 delivers
# instrument metadata ({id, title, group, precision, ...}) reliably after
# the e:98 subscription that includes it.
INSTRUMENT_LIST_EVENT: int = 1054

# Maximum time to wait for either e:1068 or e:1054 during connect().
# Raised from 15s to 180s (3 min) on 2026-06-26 to give OlympTrade's
# server more time to respond on slower connections. With 5 reconnect
# attempts, the worst-case connect-cycle time is ~15 min before Railway
# restart. If the new JWT scope change addresses the underlying issue,
# this can drop back.
ASSET_MAP_TIMEOUT_SECONDS: float = 180.0


def _normalize_key(broker_pair: str) -> str:
    """Convert broker-internal pair string to the slash form used in signals.

    Examples:
        "EURJPY" → "EUR/JPY"
        "EURJPY-OTC" → "EUR/JPY"
        "eurjpy-otc" → "EUR/JPY" (case-insensitive)
        "LATAM_X" → "LATAM_X" (no slash for non-forex assets)
    """
    base = broker_pair.upper()
    if base.endswith("-OTC"):
        base = base[: -len("-OTC")]
    if len(base) == 6 and base.isalpha():
        return f"{base[:3]}/{base[3:]}"
    return broker_pair


def _map_status(status: str | None) -> StageResult:
    """Map broker status string to StageResult literal.

    Broker status values observed in upstream logs:
      - "win"     → trade closed in profit
      - "loss"    → trade closed in loss
      - "tie"     → broker reports tie (rare; treated as loss for cascade)
      - "equal"   → alternate broker spelling of tie
      - anything else → 'error' (cascade ends with broker_unavailable)
    """
    if status == "win":
        return "win"
    if status in {"loss", "tie", "equal"}:
        return "loss"
    return "error"


class OlympTradeBroker:
    """Real broker implementation wrapping the vendored olymptrade_ws client.

    See module docstring for architecture. Lifecycle:
      - connect(): open WS, register callbacks, fetch asset map, cache
        start-of-day balance. Idempotent.
      - place(signal, *, stage, amount): resolve pair → submit trade →
        register Future → return broker trade_id.
      - wait_result(trade_id, *, timeout): await Future resolved by e:26.
      - close(): stop client, cancel pending futures. Idempotent.

    Raises:
      BrokerAuthError: token rejected, WS disconnected mid-trade, asset
        map didn't arrive, or place_order returned a malformed response.
      UnsupportedPairError: signal.pair not in the cached asset map.
    """

    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        account_group: str = "demo",
        notifier: Notifier,
        _client_factory: Callable[[], OlympTradeClient] | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("OlympTradeBroker: access_token is required")
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client_factory = _client_factory or self._default_client_factory
        self._client: OlympTradeClient | None = None
        self._assets: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._results: dict[str, dict[str, object]] = {}
        self._pending_lock = asyncio.Lock()
        self._start_of_day_balance: Decimal | None = None
        self._connected = False
        # Pre-registered Future for the e:1054 instrument list push. Captured
        # before initialize_session() so we don't miss the push if it arrives
        # during session setup. Consumed (set once) by _build_asset_map().
        self._instrument_list_future: asyncio.Future[list[object]] | None = None

    def _default_client_factory(self) -> OlympTradeClient:
        return OlympTradeClient(
            access_token=self._access_token,
            account_id=int(self._account_id) if self._account_id else None,  # type: ignore[arg-type]
            account_group=self._account_group,
            log_raw_messages=False,
        )

    async def connect(self) -> None:
        """Open WS, register push callbacks, fetch asset map, cache balance.

        Idempotent: a second call is a no-op. Raises BrokerAuthError if:
          - vendored client's WS start fails (auth rejected, network error)
          - asset map (e:1068 push) doesn't arrive within ASSET_MAP_TIMEOUT_SECONDS
          - asset map arrives but contains no usable assets
          - account_group reported by broker != configured account_group
        """
        if self._connected:
            return

        # 1. Build vendored client (sync factory call)
        self._client = self._client_factory()

        # 2. Open the WebSocket
        await self._client.start()  # type: ignore[no-untyped-call]

        # 3. Register persistent push callbacks BEFORE initialize_session
        self._client.register_callback(parameters.E_TRADE_CLOSED, self._on_trade_closed)
        self._client.register_callback(parameters.E_TRADE_ACCEPTED, self._on_trade_accepted)
        self._client.register_callback(parameters.E_TRADE_UPDATE_INTERIM, self._on_trade_interim)

        # 4. Pre-register e:1054 capture BEFORE initialize_session. The push
        #    fires shortly after the e:98 subscription that includes it
        #    (which is sent inside initialize_session). If we register after
        #    initialize_session returns, we'd miss the push.
        loop = asyncio.get_running_loop()
        self._instrument_list_future = loop.create_future()
        self._client.register_callback(INSTRUMENT_LIST_EVENT, self._on_instrument_list)

        # 5. Send startup subscriptions + account-info + balance requests
        await self._client.initialize_session()  # type: ignore[no-untyped-call]

        # 6. Build the asset map from whichever push arrives first (e:1054
        #    is the reliable fallback; e:1068 may also respond)
        await self._build_asset_map()

        # 6. Guardrail: vendored client must agree with config on account group.
        # Note: vendored library sets self.account_group only via e:1068
        # response, which never arrives for some accounts. If client.account_group
        # is None, trust the config value (we already validated via e:55).
        if (
            self._client.account_group is not None
            and self._client.account_group != self._account_group
        ):
            raise BrokerAuthError(
                f"broker reports account_group={self._client.account_group!r} "
                f"but config says {self._account_group!r}"
            )
        # Force the config value if client didn't determine it.
        if self._client.account_group is None:
            self._client.account_group = self._account_group

        # 7. Cache start-of-day balance for FR-6.3 drawdown calculation
        await self._cache_start_of_day_balance()

        self._connected = True
        _log.info(
            "OlympTradeBroker connected: account_id=%s group=%s assets=%d",
            self._account_id,
            self._account_group,
            len(self._assets),
        )

    async def _build_asset_map(self) -> None:
        """Capture the asset list from either e:1054 (instruments) or e:1068
        (account info), whichever fires first within ASSET_MAP_TIMEOUT_SECONDS.

        e:1054 is the reliable path — it's the actual instrument list and
        arrives shortly after the e:98 subscription inside initialize_session.
        e:1068 was the original event used but is marked "GUESS!" in the
        vendored library and sometimes never responds for some accounts/tokens.
        Racing both gives us resilience.

        The e:1054 capture is pre-registered in connect() (via _on_instrument_list)
        so we don't miss the push if it arrives during initialize_session.
        The e:1068 capture is registered here just-in-time (in case it arrives
        AFTER initialize_session, like the spec assumed).
        """
        client = self._client
        assert client is not None  # connect() must run before _build_asset_map

        loop = asyncio.get_running_loop()
        fut_1068: asyncio.Future[list[object]] = loop.create_future()

        async def capture_1068(message: dict[str, object]) -> None:
            if not fut_1068.done():
                d_value = message.get("d", [])
                fut_1068.set_result(d_value if isinstance(d_value, list) else [])

        client.register_callback(ASSET_LIST_EVENT, capture_1068)
        # The pre-registered e:1054 future (may already be done if the push
        # arrived during initialize_session).
        assert self._instrument_list_future is not None
        instrument_future = self._instrument_list_future

        try:
            done, pending = await asyncio.wait(
                {instrument_future, fut_1068},
                timeout=ASSET_MAP_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel any pending future's wait (the futures themselves are
            # left in their current state — set or not set).
            for p in pending:
                p.cancel()

            if not done:
                raise BrokerAuthError(
                    f"asset map: neither e:1068 nor e:1054 push arrived within "
                    f"{ASSET_MAP_TIMEOUT_SECONDS:.0f}s of initialize_session()"
                )

            winner = done.pop()
            raw_assets = winner.result()
            if winner is instrument_future:
                event_source = "e:1054 (instruments)"
            else:
                event_source = "e:1068 (account info)"

            for asset in raw_assets:
                if not isinstance(asset, dict):
                    continue
                # e:1068 data uses "pair"; e:1054 data uses "id". Try both.
                broker_pair = asset.get("pair") or asset.get("id")
                if not isinstance(broker_pair, str):
                    continue
                # e:1068 data has "cat"; e:1054 has "group" (different schema).
                # For e:1054, default to "digital" — the broker accepts
                # "digital" | "forex" | "stocks" (trade.py:26), and "digital"
                # is the safe default that works for most assets.
                category = asset.get("cat")
                if not isinstance(category, str):
                    category = "digital"
                key = _normalize_key(broker_pair)
                self._assets[key] = (broker_pair, category)

            if not self._assets:
                raise BrokerAuthError(
                    f"asset map: {event_source} push arrived but contained no usable assets"
                )

            _log.info(
                "asset map built from %s: %d entries (sample: %s)",
                event_source,
                len(self._assets),
                list(self._assets.keys())[:5],
            )
        finally:
            client.unregister_callback(ASSET_LIST_EVENT, capture_1068)

    async def _cache_start_of_day_balance(self) -> None:
        """Read the e:55 balance push and cache it for FR-6.3 drawdown.

        The vendored client stores the latest balance in `current_balance`.
        The balance update fires once at session start; we poll briefly so
        the value is populated. If not populated within 3s, set None
        (FR-6.3 then falls back to M6 placeholder behavior).
        """
        client = self._client
        assert client is not None

        # Brief delay to let the e:55 push arrive (typically <500ms)
        for _ in range(30):  # 30 * 100ms = 3s total
            if client.current_balance:
                break
            await asyncio.sleep(0.1)

        balance_msg = client.current_balance
        if not balance_msg:
            _log.warning(
                "could not read start-of-day balance from e:55 within 3s; "
                "FR-6.3 drawdown check will use 0 baseline (M6 behavior)"
            )
            self._start_of_day_balance = None
            return

        for entry in balance_msg.get("d", []):
            if isinstance(entry, dict) and entry.get("group") == self._account_group:
                balance = entry.get("balance")
                if balance is not None:
                    self._start_of_day_balance = Decimal(str(balance))
                    _log.info(
                        "start-of-day balance cached: %s %s",
                        self._start_of_day_balance,
                        self._account_group,
                    )
                    return

        _log.warning(
            "balance message arrived but no entry matches group=%s",
            self._account_group,
        )
        self._start_of_day_balance = None

    async def _on_instrument_list(self, message: dict[str, object]) -> None:
        """Persistent e:1054 callback. Captures the instrument list push
        into the pre-registered Future. Idempotent — only sets once.
        """
        if self._instrument_list_future is None or self._instrument_list_future.done():
            return
        d_value = message.get("d", [])
        self._instrument_list_future.set_result(d_value if isinstance(d_value, list) else [])

    async def _on_trade_closed(self, message: dict[str, object]) -> None:
        """Persistent e:26 callback. Resolves the matching per-trade Future.

        Race handling: e:26 may arrive BEFORE wait_result() is called. In
        that case, _pending has no entry, and we cache the payload in
        _results so wait_result's first check finds it.
        """
        trade_data = message.get("d", [])
        if not isinstance(trade_data, list) or not trade_data:
            return
        info = trade_data[0]
        if not isinstance(info, dict):
            return
        raw_id = info.get("id")
        if raw_id is None:
            return
        broker_trade_id = str(raw_id)

        status = info.get("status")
        pnl = info.get("balance_change")
        stage_result = _map_status(status if isinstance(status, str) else None)
        pnl_decimal = Decimal(str(pnl)) if pnl is not None else Decimal("0.00")

        async with self._pending_lock:
            future = self._pending.pop(broker_trade_id, None)
            payload: dict[str, object] = {"result": stage_result, "pnl": pnl_decimal}
            # Always cache so late wait_result can find it (handles the
            # race where e:26 arrives before wait_result's first check).
            self._results[broker_trade_id] = payload
            if future is not None and not future.done():
                future.set_result(payload)
        _log.info(
            "e:26 cached for late wait_result: trade_id=%s status=%s",
            broker_trade_id,
            status,
        )

    async def _on_trade_accepted(self, message: dict[str, object]) -> None:
        """e:22 — trade-placed acknowledgement from broker.

        Informational only. We already got the trade_id from place_order()'s
        response; e:22 confirms the broker registered the order.
        """
        trade_data = message.get("d", [])
        if isinstance(trade_data, list) and trade_data:
            info = trade_data[0]
            if isinstance(info, dict) and info.get("id") is not None:
                _log.info("e:22 trade accepted: trade_id=%s", info["id"])

    async def _on_trade_interim(self, message: dict[str, object]) -> None:
        """e:21 — interim trade update (live balance during the trade).

        Informational only. Does not mutate state.
        """
        trade_data = message.get("d", [])
        if isinstance(trade_data, list) and trade_data:
            info = trade_data[0]
            if isinstance(info, dict) and info.get("id") is not None:
                _log.info(
                    "e:21 trade interim: trade_id=%s interim_status=%s",
                    info["id"],
                    info.get("interim_status"),
                )

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        """Submit a trade for `signal` at `stage` for `amount` USD.

        Returns the broker's trade_id as a string. Registers a Future in
        `_pending` keyed by trade_id; the e:26 callback resolves it.

        Raises:
          BrokerAuthError: client not connected, response is None or
            missing 'id'.
          UnsupportedPairError: signal.pair not in the cached asset map.
          ConnectionError: vendored client raised it (propagated).
        """
        if not self._connected or self._client is None:
            raise BrokerAuthError("place() called before connect()")

        key = signal.pair
        if key not in self._assets:
            raise UnsupportedPairError(
                f"{key!r} not in broker asset map ({len(self._assets)} available)"
            )
        broker_pair, category = self._assets[key]
        client = self._client

        response = await client.trade.place_order(
            pair=broker_pair,
            amount=float(amount),
            direction=signal.direction,
            duration=signal.expiration_seconds,
            account_id=int(self._account_id),
            group=cast(Literal["real", "demo"], self._account_group),
            category=category,
        )

        if response is None:
            raise BrokerAuthError("place_order returned None (token rejected?)")
        trade_id = response.get("id")
        if trade_id is None:
            raise BrokerAuthError(f"place_order response missing 'id': {response!r}")
        broker_trade_id = str(trade_id)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        async with self._pending_lock:
            if broker_trade_id in self._pending:
                _log.warning(
                    "duplicate broker trade_id=%s; replacing pending future",
                    broker_trade_id,
                )
            self._pending[broker_trade_id] = future

        _log.info(
            "place: signal_id=%s pair=%s→%s stage=%s amount=%s broker_trade_id=%s",
            signal.signal_id,
            signal.pair,
            broker_pair,
            stage,
            amount,
            broker_trade_id,
        )
        return broker_trade_id

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        """Block until the broker reports a terminal result for `trade_id`.

        Returns 'win' | 'loss' | 'timeout' | 'error'. Distinguishes:
          - 'timeout' (broker connected, no e:26 within timeout)
          - ConnectionError (broker disconnected — DM-notifies, propagates)
          - 'error' (unknown trade_id, defensive)
          - CancelledError (future was cancelled, e.g. by close())
        """
        # 1. Check _results first (handles the race where e:26 arrived
        # before wait_result was called).
        async with self._pending_lock:
            if trade_id in self._results:
                payload = self._results.pop(trade_id)
                result_str = payload.get("result")
                return _map_status(result_str if isinstance(result_str, str) else None)
            future = self._pending.get(trade_id)
        if future is None:
            if not self._connected:
                raise BrokerAuthError("wait_result() called before connect()")
            _log.warning("wait_result: no pending future for trade_id=%s", trade_id)
            return "error"

        # 2. Await the future with the configured timeout
        try:
            payload = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            client = self._client
            if client is not None and not client.connection.is_connected:
                await self._notifier.on_olymp_disconnect()
                _log.warning(
                    "wait_result: broker disconnected before reporting trade_id=%s",
                    trade_id,
                )
                raise ConnectionError("olymp_disconnected") from None
            _log.warning("wait_result timeout: trade_id=%s timeout=%.1fs", trade_id, timeout)
            return "timeout"

        # 3. Clean up and map to StageResult
        async with self._pending_lock:
            self._pending.pop(trade_id, None)
        status = payload.get("result")
        return _map_status(status if isinstance(status, str) else None)

    async def close(self) -> None:
        """Stop the vendored client and cancel pending futures.

        Idempotent. Cancels any pending Futures so wait_result callers
        don't hang on the timeout. Cancelled futures are left in _pending
        so wait_result can find them and propagate CancelledError.
        """
        if not self._connected or self._client is None:
            return
        try:
            await self._client.stop()  # type: ignore[no-untyped-call]
        finally:
            self._connected = False
            async with self._pending_lock:
                for future in self._pending.values():
                    if not future.done():
                        future.cancel("OlympTradeBroker closed")
                self._results.clear()
            # Cancel any in-flight e:1054 capture (e.g., if close runs during
            # initialize_session before the push arrived).
            if self._instrument_list_future is not None and not self._instrument_list_future.done():
                self._instrument_list_future.cancel("OlympTradeBroker closed")
        _log.info("OlympTradeBroker closed")
