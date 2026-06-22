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

import logging

_log = logging.getLogger(__name__)


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
