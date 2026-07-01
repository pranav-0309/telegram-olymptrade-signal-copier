"""M13.2 tests for the real Mt5Broker impl.

All MT5 calls are mocked via `monkeypatch.setattr` on the `MetaTrader5`
class imported into `signal_copier.broker.mt5`. No real MetaTrader 5
terminal is required to run these.

The mt5linux API surface is on the `MetaTrader5` class INSTANCE — not at
module level. We therefore patch `MetaTrader5` (the class) globally so
that `Mt5Broker().connect()` instantiates a fake.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    account_info_returns: object | None = None,
    symbol_info_returns: dict[str, object | None] | None = None,
    symbols_get_returns: list[SimpleNamespace] | None = None,
    order_send_returns: object | None = None,
    positions_get_returns: list[SimpleNamespace] | None = None,
) -> MagicMock:
    """Install a fake MetaTrader5 instance into `signal_copier.broker.mt5`.

    The fake is a MagicMock assigned to the module-level `MetaTrader5` name
    so that `Mt5Broker().connect()` → `MetaTrader5()` → `fake_instance`.
    Returns the fake instance (so tests can adjust call_counts / side_effects).
    """
    from signal_copier.broker import mt5 as mt5_module

    fake_instance = MagicMock(name="MetaTrader5-instance")

    # `initialize` setup
    fake_instance.initialize.return_value = initialize_returns
    fake_instance.last_error.return_value = init_error or (0, "")
    fake_instance.account_info.return_value = account_info_returns or SimpleNamespace(
        balance=10000.0,
        leverage=500,
        currency="USD",
    )

    # Symbol resolution setup (default: EURUSD-STD present)
    _symbol_info_returns = symbol_info_returns or {"EURUSD-STD": SimpleNamespace(name="EURUSD-STD")}
    fake_instance.symbol_info.side_effect = lambda name: _symbol_info_returns.get(name)
    fake_instance.symbols_get.return_value = symbols_get_returns or []

    # Order send setup
    fake_instance.order_send.return_value = order_send_returns or SimpleNamespace(
        retcode=10009,
        comment="OK",
        order=12345,
    )

    # Positions get setup (default: no positions → wait_result returns tie)
    fake_instance.positions_get.return_value = positions_get_returns or []

    # Constants used by Mt5Broker
    fake_instance.ORDER_TYPE_BUY = 0
    fake_instance.ORDER_TYPE_SELL = 1
    fake_instance.TRADE_ACTION_DEAL = 1
    fake_instance.ORDER_FILLING_IOC = 1

    fake_instance.shutdown.return_value = None

    # Patch the MetaTrader5 class so MetaTrader5() returns our fake
    fake_class = MagicMock(return_value=fake_instance)
    monkeypatch.setattr(mt5_module, "MetaTrader5", fake_class)
    return fake_instance


@pytest.mark.asyncio
async def test_mt5_broker_connect_succeeds_with_valid_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    assert broker._mt5 is not None
    assert broker._start_of_day_balance == Decimal("10000.00")
    # verify symbol cache pre-population
    assert "EUR/USD" in broker._symbol_cache
    # verify MetaTrader5() was actually instantiated
    assert fake_instance.initialize.call_count == 1


@pytest.mark.asyncio
async def test_mt5_broker_connect_raises_broker_auth_error_on_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(
        monkeypatch,
        initialize_returns=False,
        init_error=(-10005, "IPC: No IPC connection"),
    )
    broker = _broker()
    with pytest.raises(BrokerAuthError, match="mt5.initialize failed"):
        await broker.connect()
    # mt5.initialize called 5 times (max_attempts=5 default)
    assert fake_instance.initialize.call_count == 5


@pytest.mark.asyncio
async def test_mt5_broker_connect_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.initialize.side_effect = [
        False,  # attempt 1 fails
        False,  # attempt 2 fails
        True,  # attempt 3 succeeds
    ]
    fake_instance.last_error.return_value = (-10005, "transient")
    broker = _broker()
    await broker.connect()
    assert broker._mt5 is not None
    assert fake_instance.initialize.call_count == 3


@pytest.mark.asyncio
async def test_mt5_broker_place_submits_market_order_with_lots_keyed_by_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    await broker.connect()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    ticket = await broker.place(_signal(direction="up"), stage="initial", amount=Decimal("2.00"))
    assert ticket == "12345"
    # Verify order_send was called with the right volume (0.01 for "initial")
    request = fake_instance.order_send.call_args.args[0]
    assert request["volume"] == 0.01
    assert request["symbol"] == "EURUSD-STD"
    # Direction "up" → BUY → ORDER_TYPE_BUY which we set to 0 in _install_fake_mt5
    assert request["type"] == 0  # ORDER_TYPE_BUY


@pytest.mark.asyncio
async def test_mt5_broker_place_uses_gale_lots_not_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `amount` Decimal arg is ignored — lots are keyed on stage."""
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    await broker.connect()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    # Pass 9999 USD as amount; LOTS_BY_STAGE['gale2'] = 0.04 wins
    await broker.place(_signal(), stage="gale2", amount=Decimal("9999.00"))
    request = fake_instance.order_send.call_args.args[0]
    assert request["volume"] == 0.04


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_when_symbol_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mt5(monkeypatch, symbol_info_returns={}, symbols_get_returns=[])
    broker = _broker()
    await broker.connect()
    with pytest.raises(UnsupportedPairError, match="MT5 symbol not found"):
        await broker.place(_signal(pair="ZZZ/QQQ"), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_broker_auth_error_on_no_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_instance.order_send.return_value = SimpleNamespace(
        retcode=10018,
        comment="no money",
        order=0,
    )
    broker = _broker()
    await broker.connect()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(BrokerAuthError, match="Insufficient funds"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_error_on_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_instance.order_send.return_value = SimpleNamespace(
        retcode=10006,
        comment="rejected by server",
        order=0,
    )
    broker = _broker()
    await broker.connect()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(UnsupportedPairError, match="rejected by server"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))


# -- wait_result / close_position / close (Task 7) --
#
# Real `mt5linux` is sync, so all MT5 calls are wrapped in
# `asyncio.to_thread(...)` in the impl. Assertions therefore use
# `call_count` / `assert_called_once()` (NOT `await_count` /
# `assert_awaited_once()`).


@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_win_when_position_closed_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    broker._last_known_profit["12345"] = Decimal("11.00")
    fake_instance.positions_get.return_value = []  # position gone

    result = await broker.wait_result("12345", timeout=5.0)
    assert result == "win"


@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_loss_when_position_closed_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    broker._last_known_profit["12345"] = Decimal("-2.50")
    fake_instance.positions_get.return_value = []

    result = await broker.wait_result("12345", timeout=5.0)
    assert result == "loss"


@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_timeout_on_wait_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    # Position remains open: positions_get always returns a list with one element
    fake_instance.positions_get.return_value = [SimpleNamespace(ticket=12345)]
    broker = _broker()
    await broker.connect()

    # Use a tiny timeout so the wait_for fails fast
    result = await broker.wait_result("12345", timeout=0.1)
    assert result == "timeout"


@pytest.mark.asyncio
async def test_mt5_broker_close_position_returns_decimal_profit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close_position uses opposite-direction order_send (NOT mt5.Close which
    does not exist in mt5linux). The server-side position is closed by sending
    an opposite-direction order_send with `position=<ticket>` reference."""
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.positions_get.return_value = [
        SimpleNamespace(
            profit=7.50,
            type=0,  # POSITION_TYPE_BUY=0
            volume=0.01,
            symbol="EURUSD-STD",
            ticket=12345,
        )
    ]
    fake_instance.order_send.return_value = SimpleNamespace(
        retcode=10009,
        comment="OK",
        order=99999,
    )
    broker = _broker()
    await broker.connect()

    profit = await broker.close_position("12345", timeout=5.0)
    assert profit == Decimal("7.50")
    assert broker._last_known_profit["12345"] == Decimal("7.50")
    # Verify opposite-direction order_send was called (NOT mt5.Close)
    request = fake_instance.order_send.call_args.args[0]
    assert request["position"] == 12345  # references the original ticket
    assert request["type"] == 1  # ORDER_TYPE_SELL (opposite of BUY=0)
    assert request["symbol"] == "EURUSD-STD"
    assert request["volume"] == 0.01


@pytest.mark.asyncio
async def test_mt5_broker_close_position_returns_zero_when_no_open_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If positions_get returns empty, close_position returns Decimal(0) and
    no order_send is dispatched (nothing to close)."""
    fake_instance = _install_fake_mt5(monkeypatch)
    fake_instance.positions_get.return_value = []  # no open position
    broker = _broker()
    await broker.connect()

    profit = await broker.close_position("12345", timeout=5.0)
    assert profit == Decimal("0")
    # order_send was NOT called because there's nothing to close
    fake_instance.order_send.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mt5_broker_close_calls_mt5_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instance = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    await broker.close()
    await broker.close()  # idempotent: no error on second call
    # mt5.shutdown is sync; the impl wraps it in asyncio.to_thread.
    # First close: shutdown called once. Second close: idempotent no-op.
    assert fake_instance.shutdown.call_count == 1  # type: ignore[attr-defined]
    assert broker._mt5 is None  # self._mt5 reset on close()
