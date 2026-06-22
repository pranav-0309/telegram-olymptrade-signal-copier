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
from decimal import Decimal

from olymptrade_ws import OlympTradeClient  # noqa: F401  (used by future tasks)
from signal_copier.broker.base import (  # noqa: F401  (referenced in class docstring; used by future tasks)
    BrokerAuthError,
    UnsupportedPairError,
)
from signal_copier.notify.protocol import Notifier

_log = logging.getLogger(__name__)


# Event code for the e:1068 asset-list push (per spec §5.3; not in
# olympconfig.parameters constants as a named constant).
ASSET_LIST_EVENT: int = 1068


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
    ) -> None:
        if not access_token:
            raise ValueError("OlympTradeBroker: access_token is required")
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client: OlympTradeClient | None = None
        self._assets: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._results: dict[str, dict[str, object]] = {}
        self._pending_lock = asyncio.Lock()
        self._start_of_day_balance: Decimal | None = None
        self._connected = False
