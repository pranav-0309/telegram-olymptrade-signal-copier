from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.signal import Signal


def _signal(signal_id: str = "abc123def456") -> Signal:
    """Factory for a minimal valid Signal used across dry-run broker tests.

    All numeric fields use round numbers so tests are easy to read. The
    trigger_unix_* fields are pre-computed per M2's contract (see M2 spec D-5).
    """
    return Signal(
        signal_id=signal_id,
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )


async def test_connect_logs_and_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.connect()
    await broker.connect()  # second call must not raise


async def test_close_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.close()
    await broker.close()  # second call must not raise


async def test_account_group_logged_on_connect(caplog: pytest.LogCaptureFixture) -> None:
    broker = DryRunBroker(account_group="demo")
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any("account_group=demo" in record.message for record in caplog.records)


async def test_default_account_group_is_demo(caplog: pytest.LogCaptureFixture) -> None:
    # The default constructor argument is "demo" — confirms the field default.
    broker = DryRunBroker()
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any("account_group=demo" in record.message for record in caplog.records)


async def test_place_returns_string_trade_id() -> None:
    broker = DryRunBroker()
    sig = _signal()
    trade_id = await broker.place(
        sig,
        stage="initial",
        amount=Decimal("2.00"),
    )
    assert isinstance(trade_id, str)
    assert len(trade_id) > 0


async def test_place_trade_id_has_dryrun_prefix_and_signal_id() -> None:
    broker = DryRunBroker()
    sig = _signal(signal_id="a1b2c3d4e5f6")
    trade_id = await broker.place(
        sig,
        stage="initial",
        amount=Decimal("2.00"),
    )
    assert trade_id.startswith("dryrun-a1b2c3d4e5f6-initial-")


async def test_place_logs_intended_trade(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = DryRunBroker()
    sig = _signal()
    with caplog.at_level(logging.INFO):
        trade_id = await broker.place(
            sig,
            stage="initial",
            amount=Decimal("2.00"),
        )
    assert any(
        "DRY-RUN place" in record.message
        and "EUR/JPY" in record.message
        and trade_id in record.message
        for record in caplog.records
    )
