"""M13.2 tests for the real Mt5Broker impl.

All MT5 calls are mocked via `monkeypatch.setattr` on the module-level
`mt5linux as mt5` import in `signal_copier.broker.mt5`. No real
MetaTrader 5 terminal is required to run these.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_copier.broker import Broker
from signal_copier.broker.base import BrokerAuthError, UnsupportedPairError
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.broker.mt5 import Mt5Broker
from signal_copier.domain.signal import Signal


def _signal(**overrides: object) -> Signal:
    """Build a minimal Signal for place() tests."""
    defaults: dict[str, object] = dict(
        signal_id="test-mt5",
        pair="EUR/USD",
        direction="up",
        trigger_hhmm="10:00",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EUR/USD;10:00;CALL🟩",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def _broker() -> Mt5Broker:
    return Mt5Broker(
        login=12345678,
        password="dummy",
        server="VTMarkets-Demo",
        terminal_path=None,
        notifier=None,
    )


def test_mt5_broker_satisfies_protocol() -> None:
    assert isinstance(_broker(), Broker)
    assert isinstance(DryRunBroker(), Broker)


def _install_fake_mt5(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initialize_returns: bool = True,
    init_error: tuple[int, str] | None = None,
    login_info_returns: tuple[str, str] | None = None,
    account_info_returns: object | None = None,
    symbol_info_returns: dict[str, object | None] | None = None,
    symbols_get_returns: list[SimpleNamespace] | None = None,
    order_send_returns: object | None = None,
    last_error_returns: tuple[int, str] | None = None,
) -> MagicMock:
    """Install a fake `mt5linux` module into `signal_copier.broker.mt5`.

    Returns the fake module so tests can adjust call_counts / side_effects.
    """
    fake_mt5 = MagicMock(name="mt5linux")

    fake_mt5.initialize.return_value = initialize_returns
    fake_mt5.last_error.return_value = init_error or (0, "")
    if login_info_returns is None:
        login_info_returns = ("12345678", "VTMarkets-Demo")
    fake_mt5.login_info.return_value = login_info_returns
    if account_info_returns is None:
        account_info_returns = SimpleNamespace(balance=10000.0, leverage=500, currency="USD")
    fake_mt5.account_info.return_value = account_info_returns

    if symbol_info_returns is None:
        symbol_info_returns = {"EURUSD-STD": SimpleNamespace(name="EURUSD-STD")}
    if symbol_info_returns:
        fake_mt5.symbol_info.side_effect = lambda name: symbol_info_returns.get(name)
    else:
        fake_mt5.symbol_info.return_value = None

    if symbols_get_returns is None:
        symbols_get_returns = []
    fake_mt5.symbols_get.return_value = symbols_get_returns

    if order_send_returns is None:
        # default: success, returns integer ticket 12345
        order_send_returns = SimpleNamespace(
            retcode=10009,
            comment="OK",
            order=12345,
        )
    # order_send is awaitable in the impl (`await mt5.order_send(...)`), so
    # it must be an AsyncMock for tests to capture `await_args`.
    fake_mt5.order_send = AsyncMock(return_value=order_send_returns)

    if last_error_returns is not None:
        fake_mt5.last_error.return_value = last_error_returns

    # Constants used by Mt5Broker
    fake_mt5.ORDER_TYPE_BUY = 0
    fake_mt5.ORDER_TYPE_SELL = 1
    fake_mt5.TRADE_ACTION_DEAL = 1
    fake_mt5.ORDER_FILLING_IOC = 1

    fake_mt5.positions_get.return_value = []
    fake_mt5.Close.return_value = SimpleNamespace(retcode=10009, comment="OK")
    fake_mt5.shutdown.return_value = None

    monkeypatch.setattr("signal_copier.broker.mt5.mt5", fake_mt5)
    return fake_mt5


@pytest.mark.asyncio
async def test_mt5_broker_connect_succeeds_with_valid_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    assert broker._connected is True
    assert broker._start_of_day_balance == Decimal("10000.00")
    # verify symbol cache pre-population
    assert "EUR/USD" in broker._symbol_cache


@pytest.mark.asyncio
async def test_mt5_broker_connect_raises_broker_auth_error_on_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(
        monkeypatch,
        initialize_returns=False,
        init_error=(-10005, "IPC: No IPC connection"),
    )
    broker = _broker()
    with pytest.raises(BrokerAuthError, match="mt5.initialize failed"):
        await broker.connect()
    # mt5.initialize called 5 times (max_attempts=5 default)
    assert fake_mt5.initialize.call_count == 5


@pytest.mark.asyncio
async def test_mt5_broker_connect_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.initialize.side_effect = [
        False,  # attempt 1 fails
        False,  # attempt 2 fails
        True,  # attempt 3 succeeds
    ]
    fake_mt5.last_error.return_value = (-10005, "transient")
    broker = _broker()
    await broker.connect()
    assert broker._connected is True
    assert fake_mt5.initialize.call_count == 3


@pytest.mark.asyncio
async def test_mt5_broker_place_submits_market_order_with_lots_keyed_by_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    ticket = await broker.place(_signal(direction="up"), stage="initial", amount=Decimal("2.00"))
    assert ticket == "12345"
    # Verify order_send was called with the right volume (0.01 for "initial")
    request = fake_mt5.order_send.await_args.args[0]
    assert request["volume"] == 0.01
    assert request["symbol"] == "EURUSD-STD"
    # Direction "up" → BUY → mt5.ORDER_TYPE_BUY which we set to 0 in _install_fake_mt5
    assert request["type"] == 0  # mt5.ORDER_TYPE_BUY


@pytest.mark.asyncio
async def test_mt5_broker_place_uses_gale_lots_not_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `amount` Decimal arg is ignored — lots are keyed on stage."""
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    # Pass 9999 USD as amount; LOTS_BY_STAGE['gale2'] = 0.04 wins
    await broker.place(_signal(), stage="gale2", amount=Decimal("9999.00"))
    request = fake_mt5.order_send.await_args.args[0]
    assert request["volume"] == 0.04


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_when_symbol_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mt5(monkeypatch, symbol_info_returns={}, symbols_get_returns=[])
    broker = _broker()
    with pytest.raises(UnsupportedPairError, match="MT5 symbol not found"):
        await broker.place(_signal(pair="ZZZ/QQQ"), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_broker_auth_error_on_no_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_mt5.order_send.return_value = SimpleNamespace(
        retcode=10018,
        comment="no money",
        order=0,
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(BrokerAuthError, match="Insufficient funds"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_error_on_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_mt5.order_send.return_value = SimpleNamespace(
        retcode=10006,
        comment="rejected by server",
        order=0,
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(UnsupportedPairError, match="rejected by server"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))
