"""Shared test fixtures for M8's OlympTradeBroker tests.

Helpers:
  - FakeOlympTradeClient: duck-typed stub for olymptrade_ws.OlympTradeClient.
    Records place_order calls; exposes _deliver_event(event_code, payload)
    to simulate push events; supports connection.is_connected polling.
  - FakeConnection: stub for vendored Connection class (.is_connected property).
  - FakeTradeAPI: stub for vendored TradeAPI; .place_order(...) recorder.
  - make_signal: factory for a minimal valid Signal used across tests.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from signal_copier.domain.signal import Signal


class FakeConnection:
    """Stub for olymptrade_ws.core.connection.Connection."""

    def __init__(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class FakeTradeAPI:
    """Stub for olymptrade_ws.api.trade.TradeAPI. .place_order(...) recorder."""

    def __init__(self, client: FakeOlympTradeClient) -> None:
        self._client = client
        self.place_order_calls: list[dict[str, Any]] = []
        self.next_response: dict[str, Any] | None = None
        self.raise_on_call: BaseException | None = None

    async def place_order(self, **kwargs: Any) -> dict[str, Any] | None:
        self.place_order_calls.append(kwargs)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.next_response is not None:
            return self.next_response
        self._client._next_trade_id += 1
        return {"id": self._client._next_trade_id, "status": "open"}


class FakeOlympTradeClient:
    """Duck-typed stub for olymptrade_ws.OlympTradeClient used by M8 tests.

    Records place_order calls; exposes _deliver_event(event_code, payload) to
    simulate push events. Supports connection.is_connected polling for the
    disconnect-detection tests.
    """

    def __init__(
        self,
        *,
        account_group: str = "demo",
        account_id: int = 12345,
    ) -> None:
        self.account_group = account_group
        self.account_id = account_id
        self.connection = FakeConnection()
        self._callbacks: dict[int, list[Callable[..., Any]]] = defaultdict(list)
        self.trade = FakeTradeAPI(self)
        self.current_balance: dict[str, Any] | None = None
        self.start_called = False
        self.stop_called = False
        self.initialize_session_called = False
        self._next_trade_id = 1000

    async def start(self) -> None:
        self.start_called = True
        self.connection._connected = True

    async def stop(self) -> None:
        self.stop_called = True
        self.connection._connected = False

    async def initialize_session(self) -> None:
        self.initialize_session_called = True

    def register_callback(self, code: int, cb: Callable[..., Any]) -> None:
        self._callbacks[code].append(cb)

    def unregister_callback(self, code: int, cb: Callable[..., Any]) -> None:
        self._callbacks[code].remove(cb)

    async def _deliver_event(self, event_code: int, payload: dict[str, Any]) -> None:
        """Test helper: deliver a push event as if from the broker."""
        for cb in self._callbacks.get(event_code, []):
            await cb(payload)


def make_signal(
    *,
    signal_id: str = "test-sig-1",
    pair: str = "EUR/JPY",
    direction: str = "down",
    expiration_seconds: int = 300,
) -> Signal:
    """Build a minimal valid Signal used across broker tests."""
    return Signal(
        signal_id=signal_id,
        pair=pair,
        direction=direction,  # type: ignore[arg-type]
        trigger_hhmm="10:20",
        expiration_seconds=expiration_seconds,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text=f"{pair};10:20;PUT🟥",
        trigger_unix_initial=1_700_000_300.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )


def make_balance_message(
    *, account_group: str = "demo", balance: float = 10000.0
) -> dict[str, Any]:
    """Build a fake e:55 balance push for our account_group."""
    return {"d": [{"group": account_group, "balance": balance}]}
