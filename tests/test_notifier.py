"""Tests for signal_copier.notify.protocol — the Notifier Protocol + NoOpNotifier.

M6 ships the Protocol + NoOpNotifier (logs at INFO). M7's TelegramDMNotifier
implements the same Protocol and sends real Telegram DMs. RecordingNotifier
(in tests/_scheduler_fixtures.py) is used by test_scheduler.py.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest

from signal_copier.domain.signal import Signal
from signal_copier.infra.db_rows import DailySummaryRow
from signal_copier.notify.protocol import NoOpNotifier, Notifier


def _make_signal() -> Signal:
    return Signal(
        signal_id="test-sig-1",
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_000_000.0,
        source_message_id=42,
        source_chat_id=-100,
        raw_text="(test)",
        trigger_unix_initial=1_001_000.0,
        trigger_unix_gale1=1_001_300.0,
        trigger_unix_gale2=1_001_600.0,
    )


# --- Protocol runtime checkability -----------------------------------------


def test_protocol_isinstance_noop() -> None:
    """NoOpNotifier satisfies the Notifier Protocol structurally."""
    assert isinstance(NoOpNotifier(), Notifier)


def test_protocol_isinstance_plain_object_fails() -> None:
    """A plain object that doesn't implement the methods is not a Notifier."""
    assert not isinstance(object(), Notifier)


# --- NoOpNotifier log payloads ---------------------------------------------


@pytest.mark.asyncio
async def test_noop_notifier_logs_signal_received(
    caplog: pytest.LogCaptureFixture,
) -> None:
    signal = _make_signal()
    with caplog.at_level(logging.INFO, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_signal_received(signal)
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "event=signal_received" in msg
    assert "signal_id=test-sig-1" in msg
    assert "pair=EUR/JPY" in msg
    assert "direction=down" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_trade_placed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    signal = _make_signal()
    with caplog.at_level(logging.INFO, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_trade_placed(
            signal,
            stage="initial",
            amount=Decimal("2.00"),
            trade_id="t-1",
        )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "event=trade_placed" in msg
    assert "stage=initial" in msg
    assert "amount=2.00" in msg
    assert "trade_id=t-1" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_loss_with_next_stage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    signal = _make_signal()
    with caplog.at_level(logging.INFO, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_loss(
            signal,
            stage="initial",
            pnl=Decimal("-2.00"),
            cumulative_pnl=Decimal("-2.00"),
            next_stage="gale1",
        )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "event=loss" in msg
    assert "next_stage=gale1" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_bot_started(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_bot_started(
            mode="dry_run",
            watching="@analyst",
            timezone="America/Sao_Paulo",
        )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "event=bot_started" in msg
    assert "mode=dry_run" in msg
    assert "watching=@analyst" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_signal_rejected_by_limit_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """signal_rejected_by_limit is the only method that logs at WARNING
    (it's a halt condition; the user needs to see it)."""
    signal = _make_signal()
    summary = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=10,
        trades_count=10,
        wins=2,
        losses=8,
        realized_pnl=Decimal("-50.00"),
        limit_hit="loss",
    )
    with caplog.at_level(logging.WARNING, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_signal_rejected_by_limit(
            signal,
            limit_type="loss",
            summary=summary,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    msg = caplog.records[0].getMessage()
    assert "event=signal_rejected_by_limit" in msg
    assert "limit_type=loss" in msg
