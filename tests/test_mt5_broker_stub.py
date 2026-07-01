"""M13.1 stub broker tests. M13.2 replaces stub with the real Mt5Broker."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from signal_copier.broker import Broker
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.broker.mt5 import Mt5Broker
from signal_copier.domain.signal import Signal


def _broker() -> Mt5Broker:
    return Mt5Broker(
        login=12345678,
        password="dummy",
        server="VTMarkets-Demo",
        terminal_path=None,
        notifier=None,
    )


def test_mt5_broker_satisfies_protocol() -> None:
    """isinstance(Mt5Broker(), Broker) must be True so __main__ can wire it.

    Tests both the new Mt5Broker and the existing DryRunBroker
    to confirm no regression in Protocol coverage.
    """
    assert isinstance(_broker(), Broker)
    assert isinstance(DryRunBroker(), Broker)


async def test_mt5_broker_connect_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13\\.2"):
        await _broker().connect()


async def test_mt5_broker_place_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13\\.2"):
        await _broker().place(
            signal=_make_signal(),
            stage="initial",
            amount=Decimal("0.01"),
        )


async def test_mt5_broker_wait_result_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13\\.2"):
        await _broker().wait_result("dummy-trade", timeout=5.0)


async def test_mt5_broker_close_position_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13\\.2"):
        await _broker().close_position("dummy-trade", timeout=5.0)


async def test_mt5_broker_close_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13\\.2"):
        await _broker().close()


def test_mt5_broker_init_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Stub logs at WARNING so misconfigured deployments are visible."""
    with caplog.at_level(logging.WARNING):
        _broker()
    assert any("stub class" in record.message for record in caplog.records)


def _make_signal() -> Signal:
    """Minimal Signal stub — used only to satisfy the place() signature."""
    return Signal(
        signal_id="test-mt5-stub",
        pair="EURUSD",
        direction="up",
        trigger_hhmm="10:00",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EURUSD;10:00;CALL🟩",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )
