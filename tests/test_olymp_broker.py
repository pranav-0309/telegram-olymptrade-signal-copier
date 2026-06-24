from __future__ import annotations

import asyncio
import logging
import unittest.mock
from decimal import Decimal
from typing import cast

import pytest

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters
from signal_copier.broker.base import BrokerAuthError, UnsupportedPairError
from signal_copier.broker.olymp import (
    ASSET_LIST_EVENT,
    OlympTradeBroker,
    _map_status,
    _normalize_key,
)
from tests._broker_fixtures import FakeOlympTradeClient, make_signal
from tests._scheduler_fixtures import RecordingNotifier


def _attach_fake_client(broker: OlympTradeBroker, fake: FakeOlympTradeClient) -> None:
    """Inject a FakeOlympTradeClient into a broker in lieu of running connect().

    The cast keeps mypy strict-mode happy: the broker's _client field is typed
    as OlympTradeClient | None, but a duck-typed fake is the test's substitute.
    """
    broker._client = cast(OlympTradeClient, fake)


def test_normalize_key_handles_plain() -> None:
    assert _normalize_key("EURJPY") == "EUR/JPY"


def test_normalize_key_handles_otc_suffix() -> None:
    assert _normalize_key("EURJPY-OTC") == "EUR/JPY"


def test_normalize_key_handles_lowercase() -> None:
    assert _normalize_key("eurjpy") == "EUR/JPY"


def test_normalize_key_handles_lowercase_otc() -> None:
    # The lowercase suffix must be normalized to uppercase first
    assert _normalize_key("eurjpy-otc") == "EUR/JPY"


def test_normalize_key_passes_through_unknown_shape() -> None:
    assert _normalize_key("LATAM_X") == "LATAM_X"


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


def test_constructor_rejects_empty_access_token() -> None:
    from signal_copier.notify.protocol import NoOpNotifier

    with pytest.raises(ValueError, match="access_token"):
        OlympTradeBroker(
            access_token="",
            account_id="12345",
            account_group="demo",
            notifier=NoOpNotifier(),
        )


def test_constructor_initializes_state(notifier: RecordingNotifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    assert broker._connected is False
    assert broker._client is None
    assert broker._assets == {}
    assert broker._pending == {}
    assert broker._results == {}
    assert broker._start_of_day_balance is None


def test_constructor_stores_config(notifier: RecordingNotifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="99999",
        account_group="real",
        notifier=notifier,
    )
    assert broker._access_token == "fake"
    assert broker._account_id == "99999"
    assert broker._account_group == "real"


def test_map_status_win() -> None:
    assert _map_status("win") == "win"


def test_map_status_loss() -> None:
    assert _map_status("loss") == "loss"


def test_map_status_tie_becomes_loss() -> None:
    # FR-5.3: tie treated as loss for cascade purposes
    assert _map_status("tie") == "loss"


def test_map_status_equal_becomes_loss() -> None:
    # Alternate broker spelling of tie
    assert _map_status("equal") == "loss"


def test_map_status_unknown_returns_error() -> None:
    assert _map_status("weird") == "error"


def test_map_status_none_returns_error() -> None:
    assert _map_status(None) == "error"


def _make_broker(
    notifier: RecordingNotifier,
    *,
    fake_client: FakeOlympTradeClient,
    account_group: str = "demo",
) -> OlympTradeBroker:
    """Build a broker wired to a fake client (no I/O)."""
    return OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group=account_group,
        notifier=notifier,
        _client_factory=lambda: fake_client,  # type: ignore[arg-type, return-value]
    )


async def _async_noop() -> None:
    return None


async def test_connect_is_idempotent(notifier: RecordingNotifier) -> None:
    """Second connect() does not re-call fake_client.start()."""
    fake_client = FakeOlympTradeClient()
    state = {"start_calls": 0}
    real_start = fake_client.start

    async def counting_start() -> None:
        state["start_calls"] += 1
        await real_start()

    fake_client.start = counting_start  # type: ignore[method-assign]

    broker = _make_broker(notifier, fake_client=fake_client)
    setattr(broker, "_build_asset_map", _async_noop)  # noqa: B010
    setattr(broker, "_cache_start_of_day_balance", _async_noop)  # noqa: B010

    await broker.connect()
    await broker.connect()
    assert state["start_calls"] == 1


async def test_build_asset_map_populates_assets(notifier: RecordingNotifier) -> None:
    """Captures the e:1068 push and populates the asset map."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)  # normally set by connect()

    # Schedule an e:1068 delivery to fire shortly after _build_asset_map starts
    async def deliver_assets() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(
            ASSET_LIST_EVENT,
            {"d": [{"pair": "EURJPY", "cat": "forex"}, {"pair": "GBPUSD-OTC", "cat": "otc"}]},
        )

    asyncio.create_task(deliver_assets())
    await broker._build_asset_map()

    assert broker._assets == {
        "EUR/JPY": ("EURJPY", "forex"),
        "GBP/USD": ("GBPUSD-OTC", "otc"),
    }


async def test_build_asset_map_timeout_raises_broker_auth_error(
    notifier: RecordingNotifier,
) -> None:
    """No e:1068 push within 15s → BrokerAuthError.

    We patch asyncio.wait_for to simulate the timeout without actually waiting 15s.
    """
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)  # normally set by connect()
    import unittest.mock as _mock

    with (
        _mock.patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        pytest.raises(BrokerAuthError, match="asset map"),
    ):
        await broker._build_asset_map()


async def test_build_asset_map_empty_raises_broker_auth_error(
    notifier: RecordingNotifier,
) -> None:
    """e:1068 arrives with empty list → BrokerAuthError."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)  # normally set by connect()

    async def deliver_empty() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(ASSET_LIST_EVENT, {"d": []})

    asyncio.create_task(deliver_empty())
    with pytest.raises(BrokerAuthError, match="no usable assets"):
        await broker._build_asset_map()


async def test_build_asset_map_skips_malformed_entries(notifier: RecordingNotifier) -> None:
    """Entries missing 'pair' are skipped; valid entries still land in the map."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)  # normally set by connect()

    async def deliver_mixed() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(
            ASSET_LIST_EVENT,
            {
                "d": [
                    {"pair": "EURJPY", "cat": "forex"},
                    {"cat": "forex"},  # missing 'pair'
                    "not-a-dict",
                    {"pair": "GBPUSD", "cat": "forex"},
                ]
            },
        )

    asyncio.create_task(deliver_mixed())
    await broker._build_asset_map()

    assert broker._assets == {
        "EUR/JPY": ("EURJPY", "forex"),
        "GBP/USD": ("GBPUSD", "forex"),
    }


async def test_cache_start_of_day_balance_success(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.current_balance = {"d": [{"group": "demo", "balance": 10000.0}]}
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance == Decimal("10000.0")


async def test_cache_start_of_day_balance_timeout_leaves_none(
    notifier: RecordingNotifier,
) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.current_balance = None
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    # Speed up the test by patching the module-level asyncio.sleep
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        await real_sleep(0.001)

    with unittest.mock.patch("signal_copier.broker.olymp.asyncio.sleep", side_effect=fast_sleep):
        await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance is None


async def test_cache_start_of_day_balance_skips_wrong_group(
    notifier: RecordingNotifier,
) -> None:
    fake_client = FakeOlympTradeClient()
    # Broker reports real, but we configured demo
    fake_client.current_balance = {"d": [{"group": "real", "balance": 5000.0}]}
    broker = _make_broker(notifier, fake_client=fake_client, account_group="demo")
    _attach_fake_client(broker, fake_client)
    await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance is None


# --- place() tests (Task 9) -----------------------------------------------


async def test_place_resolves_pair_via_asset_map(notifier: RecordingNotifier) -> None:
    """EUR/JPY → fake.place_order called with pair='EURJPY', category='forex'."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal(pair="EUR/JPY", direction="down")
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    assert len(fake_client.trade.place_order_calls) == 1
    call = fake_client.trade.place_order_calls[0]
    assert call["pair"] == "EURJPY"
    assert call["category"] == "forex"
    assert call["direction"] == "down"
    assert call["amount"] == 2.00  # float conversion for vendored client
    assert trade_id == str(call["id"])


async def test_place_otc_pair_resolves_correctly(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY-OTC", "otc")}

    sig = make_signal(pair="EUR/JPY", direction="up")
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    call = fake_client.trade.place_order_calls[0]
    assert call["pair"] == "EURJPY-OTC"
    assert call["category"] == "otc"
    assert isinstance(trade_id, str)


async def test_place_unsupported_pair_raises(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal(pair="USD/EGP")
    with pytest.raises(UnsupportedPairError, match="USD/EGP"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_records_pending_future(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    assert trade_id in broker._pending
    assert broker._pending[trade_id] is not None


async def test_place_returns_broker_trade_id_as_string(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = {"id": 12345, "status": "open"}
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert trade_id == "12345"


async def test_place_none_response_raises_broker_auth_error(
    notifier: RecordingNotifier,
) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = None
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="returned None"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_missing_id_in_response_raises(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = {"status": "win"}
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="missing 'id'"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_connection_error_propagates(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.raise_on_call = ConnectionError("WS down")
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(ConnectionError, match="WS down"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_before_connect_raises_broker_auth_error(
    notifier: RecordingNotifier,
) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    # Note: no _client wiring, no _connected=True
    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="before connect"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


# --- _on_trade_closed tests (Task 10) --------------------------------------


async def test_on_trade_closed_resolves_pending_future(
    notifier: RecordingNotifier,
) -> None:
    """Delivering e:26 with matching trade_id resolves the pending Future."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    future = broker._pending[trade_id]  # capture BEFORE delivery

    # Deliver e:26 BEFORE wait_result — covers the race
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
    )

    assert future.done()
    result = future.result()
    assert result["result"] == "win"
    assert result["pnl"] == Decimal("1.84")


async def test_on_trade_closed_caches_when_no_pending(notifier: RecordingNotifier) -> None:
    """Delivering e:26 with NO pending entry caches to _results for late wait_result."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)

    # No place() called — _pending is empty
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 99999, "status": "loss", "balance_change": -2.0}]},
    )

    assert "99999" in broker._results
    assert broker._results["99999"]["result"] == "loss"


async def test_on_trade_closed_ignores_empty_d_list(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)

    await fake_client._deliver_event(parameters.E_TRADE_CLOSED, {"d": []})
    # No exception; no state mutation
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_closed_ignores_missing_id(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)

    await fake_client._deliver_event(parameters.E_TRADE_CLOSED, {"d": [{"status": "win"}]})
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_closed_ignores_duplicate_delivery(
    notifier: RecordingNotifier,
) -> None:
    """Second e:26 for the same trade_id is a no-op (WARNING logged)."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)

    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 12345, "status": "win", "balance_change": 1.84}]},
    )
    assert "12345" in broker._results

    # Second delivery — _results already has it
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 12345, "status": "win", "balance_change": 1.84}]},
    )
    # No exception
    assert "12345" in broker._results


# --- _on_trade_accepted / _on_trade_interim tests (Task 11) ---------------


async def test_on_trade_accepted_logs_only(
    notifier: RecordingNotifier, caplog: pytest.LogCaptureFixture
) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_ACCEPTED, broker._on_trade_accepted)
    broker._connected = True

    with caplog.at_level(logging.INFO):
        await fake_client._deliver_event(
            parameters.E_TRADE_ACCEPTED,
            {"d": [{"id": 12345}]},
        )

    assert any("e:22" in record.message for record in caplog.records)
    # No state mutation
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_interim_logs_only(
    notifier: RecordingNotifier, caplog: pytest.LogCaptureFixture
) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_UPDATE_INTERIM, broker._on_trade_interim)
    broker._connected = True

    with caplog.at_level(logging.INFO):
        await fake_client._deliver_event(
            parameters.E_TRADE_UPDATE_INTERIM,
            {"d": [{"id": 12345, "interim_status": "open"}]},
        )

    assert any("e:21" in record.message for record in caplog.records)
    assert broker._pending == {}
    assert broker._results == {}


# --- wait_result() tests (Task 12) -----------------------------------------


async def test_wait_result_resolves_on_e26_win(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "win"


async def test_wait_result_resolves_on_e26_loss(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "loss", "balance_change": -2.0}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "loss"


async def test_wait_result_resolves_on_e26_tie(notifier: RecordingNotifier) -> None:
    """tie → loss (FR-5.3 cascade treatment)."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "tie"}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "loss"


async def test_wait_result_resolves_after_e26_already_arrived(
    notifier: RecordingNotifier,
) -> None:
    """Race recovery: e:26 cached in _results when wait_result is called."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    # Deliver e:26 BEFORE wait_result
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
    )

    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "win"
    # _results popped
    assert trade_id not in broker._results


async def test_wait_result_timeout_when_connected_returns_timeout(
    notifier: RecordingNotifier,
) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.connection._connected = True
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    result = await broker.wait_result(trade_id, timeout=0.05)
    assert result == "timeout"


async def test_wait_result_timeout_when_disconnected_raises(
    notifier: RecordingNotifier,
) -> None:
    """Disconnection mid-trade → ConnectionError after DM-notify on_olymp_disconnect."""
    fake_client = FakeOlympTradeClient()
    fake_client.connection._connected = False  # broker disconnected
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    with pytest.raises(ConnectionError, match="olymp_disconnected"):
        await broker.wait_result(trade_id, timeout=0.05)

    # Notifier was called
    method_names = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in method_names


async def test_wait_result_unknown_trade_id_returns_error(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True

    result = await broker.wait_result("nope", timeout=2.0)
    assert result == "error"


async def test_wait_result_before_connect_raises(notifier: RecordingNotifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    with pytest.raises(BrokerAuthError, match="before connect"):
        await broker.wait_result("12345", timeout=2.0)


# --- close() tests (Task 13) -----------------------------------------------


async def test_close_is_idempotent(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True

    await broker.close()
    await broker.close()  # second call must not raise
    assert fake_client.stop_called is True  # only once


async def test_close_stops_underlying_client(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True

    await broker.close()
    assert fake_client.stop_called is True
    assert broker._connected is False


async def test_close_cancels_pending_futures(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    fake_client.register_callback(parameters.E_TRADE_CLOSED, broker._on_trade_closed)
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    await broker.close()

    # The Future is cancelled; wait_result should raise CancelledError
    with pytest.raises(asyncio.CancelledError):
        await broker.wait_result(trade_id, timeout=1.0)


async def test_close_clears_results_cache(notifier: RecordingNotifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    _attach_fake_client(broker, fake_client)
    broker._connected = True
    broker._results["some_id"] = {"result": "win", "pnl": Decimal("1.0")}

    await broker.close()
    assert broker._results == {}


async def test_close_without_connect_is_safe(notifier: RecordingNotifier) -> None:
    """close() before connect() is a no-op."""
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    await broker.close()
    assert broker._connected is False


# --- connect() callback registration tests (Task 14) ----------------------


async def test_connect_registers_three_callbacks(notifier: RecordingNotifier) -> None:
    """connect() registers e:21/e:22/e:26 callbacks on the vendored client."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    setattr(broker, "_build_asset_map", _async_noop)  # noqa: B010
    setattr(broker, "_cache_start_of_day_balance", _async_noop)  # noqa: B010

    await broker.connect()

    assert any(
        cb == broker._on_trade_closed
        for cb in fake_client._callbacks.get(parameters.E_TRADE_CLOSED, [])
    )
    assert any(
        cb == broker._on_trade_accepted
        for cb in fake_client._callbacks.get(parameters.E_TRADE_ACCEPTED, [])
    )
    assert any(
        cb == broker._on_trade_interim
        for cb in fake_client._callbacks.get(parameters.E_TRADE_UPDATE_INTERIM, [])
    )


async def test_connect_calls_initialize_session(notifier: RecordingNotifier) -> None:
    """connect() calls initialize_session() on the vendored client."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    setattr(broker, "_build_asset_map", _async_noop)  # noqa: B010
    setattr(broker, "_cache_start_of_day_balance", _async_noop)  # noqa: B010

    await broker.connect()

    assert fake_client.initialize_session_called is True


async def test_connect_account_group_mismatch_raises(notifier: RecordingNotifier) -> None:
    """Broker reports different account_group than configured → BrokerAuthError."""
    fake_client = FakeOlympTradeClient(account_group="real")
    broker = _make_broker(notifier, fake_client=fake_client, account_group="demo")
    setattr(broker, "_build_asset_map", _async_noop)  # noqa: B010
    setattr(broker, "_cache_start_of_day_balance", _async_noop)  # noqa: B010

    with pytest.raises(BrokerAuthError, match="account_group"):
        await broker.connect()
