from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


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


async def test_wait_result_default_returns_win() -> None:
    broker = DryRunBroker()
    sig = _signal()
    for stage in ("initial", "gale1", "gale2"):
        tid = await broker.place(
            sig,
            stage=stage,
            amount=Decimal("2.00"),
        )
        result = await broker.wait_result(tid, timeout=330.0)
        assert result == "win"


async def test_wait_result_uses_custom_provider() -> None:
    async def loss_all(s: Signal, st: Stage) -> StageResult:
        return "loss"

    broker = DryRunBroker(outcome_provider=loss_all)
    sig = _signal()
    tid = await broker.place(
        sig,
        stage="initial",
        amount=Decimal("2.00"),
    )
    result = await broker.wait_result(tid, timeout=330.0)
    assert result == "loss"


async def test_wait_result_provider_receives_signal_and_stage() -> None:
    captured: list[tuple[Signal, Stage]] = []

    async def capture(s: Signal, st: Stage) -> StageResult:
        captured.append((s, st))
        return "win"

    broker = DryRunBroker(outcome_provider=capture)
    sig = _signal()
    tid = await broker.place(
        sig,
        stage="gale1",
        amount=Decimal("4.00"),
    )
    await broker.wait_result(tid, timeout=330.0)
    assert len(captured) == 1
    assert captured[0][0] is sig
    assert captured[0][1] == "gale1"


async def test_place_then_wait_pops_trade_id_dict() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid = await broker.place(
        sig,
        stage="initial",
        amount=Decimal("2.00"),
    )
    assert tid in broker._placed
    await broker.wait_result(tid, timeout=330.0)
    assert tid not in broker._placed


async def test_multiple_in_flight_places_do_not_collide() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid1 = await broker.place(
        sig,
        stage="initial",
        amount=Decimal("2.00"),
    )
    tid2 = await broker.place(
        sig,
        stage="gale1",
        amount=Decimal("4.00"),
    )
    assert tid1 != tid2
    assert await broker.wait_result(tid1, timeout=330.0) == "win"
    assert await broker.wait_result(tid2, timeout=330.0) == "win"
    assert broker._placed == {}  # both popped


async def test_wait_result_unknown_trade_id_returns_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = DryRunBroker()
    with caplog.at_level(logging.WARNING):
        result = await broker.wait_result("unknown-id", timeout=330.0)
    assert result == "error"
    assert any("unknown trade_id" in record.message for record in caplog.records)
