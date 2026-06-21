"""Tests for signal_copier.notify.telegram_dm — TelegramDMNotifier."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import Signal
from signal_copier.notify.protocol import Notifier
from signal_copier.notify.telegram_dm import TelegramDMNotifier

# --- Test fixtures ---------------------------------------------------------


@dataclass
class FakeTgClient:
    """Duck-typed TelegramClient — only the surface TelegramDMNotifier uses."""

    sent: list[str] = field(default_factory=list)
    raise_on_send: BaseException | None = None

    async def send_to_self(self, text: str) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(text)


def _make_config(**overrides: Any) -> Config:
    """Build a Config for tests. Pass kwargs to override defaults.

    Example: _make_config(daily_loss_limit=Decimal("50.00"))
    """
    defaults: dict[str, Any] = {"timezone": "America/Sao_Paulo"}
    defaults.update(overrides)
    return Config(**defaults)


def _make_signal(**overrides: Any) -> Signal:
    """Build a Signal with a trigger_unix_initial that maps to 10:20 BRT."""
    from datetime import datetime

    trigger = datetime(2026, 6, 21, 13, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    defaults: dict[str, Any] = dict(
        signal_id="sig-abc",
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_750_000_000.0,
        source_message_id=42,
        source_chat_id=-100,
        raw_text="(test)",
        trigger_unix_initial=trigger,
        trigger_unix_gale1=trigger + 300,
        trigger_unix_gale2=trigger + 600,
    )
    defaults.update(overrides)
    return Signal(**defaults)


# --- Skeleton tests --------------------------------------------------------


def test_satisfies_notifier_protocol() -> None:
    """TelegramDMNotifier must implement the full Notifier Protocol."""
    from signal_copier.notify.telegram_dm import TelegramDMNotifier

    notifier = TelegramDMNotifier(tg_client=FakeTgClient(), config=_make_config())
    assert isinstance(notifier, Notifier)


@pytest.mark.asyncio
async def test_send_failure_logged_and_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When send_to_self raises, the method must not raise (D-5)."""
    from loguru import logger as _loguru_logger

    _loguru_logger.remove()

    from signal_copier.notify.telegram_dm import TelegramDMNotifier

    fake = FakeTgClient(raise_on_send=ConnectionError("simulated"))
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())

    with caplog.at_level(logging.WARNING):
        await notifier.on_telegram_disconnect()
    assert fake.sent == []


@pytest.mark.asyncio
async def test_signal_received() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_received(signal)
    assert len(fake.sent) == 1
    expected = (
        "🟢 Signal received\n"
        "Pair: EUR/JPY\n"
        "Direction: PUT\n"
        "Trigger: 10:20 (UTC-3)\n"
        "Expiration: 5 min"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_bot_started() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    await notifier.on_bot_started(mode="dry_run", watching="@analyst", timezone="America/Sao_Paulo")
    assert len(fake.sent) == 1
    expected = (
        "🟢 Bot started\n" "Mode: dry_run\n" "Watching: @analyst\n" "Timezone: America/Sao_Paulo"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_bot_stopping() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    await notifier.on_bot_stopping(open_cascades=3)
    assert len(fake.sent) == 1
    expected = "🔴 Bot stopping\nOpen cascades: 3"
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_trade_placed_initial() -> None:
    from decimal import Decimal

    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_trade_placed(
        signal, stage="initial", amount=Decimal("2.00"), trade_id="abc123"
    )
    expected = (
        "⏱️ Trade placed (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "Direction: PUT\n"
        "Amount: $2.00\n"
        "Expires: 10:25 (UTC-3)\n"
        "Trade ID: abc123"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_trade_placed_gale1() -> None:
    from decimal import Decimal

    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="up")
    await notifier.on_trade_placed(signal, stage="gale1", amount=Decimal("4.00"), trade_id="def456")
    expected = (
        "⏱️ Trade placed (1st GALE)\n"
        "Amount: $4.00\n"
        "Expires: 10:30 (UTC-3)\n"
        "Triggered by: loss on initial\n"
        "Trade ID: def456"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_trade_placed_gale2() -> None:
    from decimal import Decimal

    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_trade_placed(signal, stage="gale2", amount=Decimal("8.00"), trade_id="ghi789")
    expected = (
        "⏱️ Trade placed (2nd GALE)\n"
        "Amount: $8.00\n"
        "Expires: 10:35 (UTC-3)\n"
        "Triggered by: loss on 1st gale\n"
        "Trade ID: ghi789"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_win_initial() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="initial",
        pnl=Decimal("1.84"),
        cumulative_pnl=Decimal("1.84"),
    )
    expected = (
        "✅ WIN (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$1.84\n"
        "Signal closed: done_win\n"
        "Next: stop (cascade ends)"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_win_gale1() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="gale1",
        pnl=Decimal("3.68"),
        cumulative_pnl=Decimal("1.68"),
    )
    expected = (
        "✅ WIN (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$3.68\n"
        "Cascade: stopped after gale1 — total recovered"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_win_gale2() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="gale2",
        pnl=Decimal("7.36"),
        cumulative_pnl=Decimal("5.36"),
    )
    expected = (
        "✅ WIN (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$7.36\n"
        "Cascade: stopped after gale2 — full recovery"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_initial_with_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="initial",
        pnl=Decimal("-2.00"),
        cumulative_pnl=Decimal("-2.00"),
        next_stage="gale1",
    )
    expected = (
        "❌ LOSS (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-2.00\n"
        "Next: scheduling 1st gale at 10:25 (UTC-3), $4.00"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_gale1_with_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="gale1",
        pnl=Decimal("-4.00"),
        cumulative_pnl=Decimal("-6.00"),
        next_stage="gale2",
    )
    expected = (
        "❌ LOSS (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-4.00\n"
        "Next: scheduling 2nd gale at 10:30 (UTC-3), $8.00"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_gale2_no_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="gale2",
        pnl=Decimal("-8.00"),
        cumulative_pnl=Decimal("-14.00"),
        next_stage=None,
    )
    # fmt: off
    expected = (
        "❌ LOSS (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-8.00\n"
        "Cascade: ended — full loss ($-14.00 total)"
    )
    # fmt: on
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_initial_uses_configured_gale_amount() -> None:
    """If AMOUNT_GALE1 is overridden in config, the DM must show the
    configured amount, not the hardcoded default."""
    from decimal import Decimal

    from signal_copier.config import Config

    fake = FakeTgClient()
    # Custom config: gale1 = $5.00 (instead of the $4.00 default)
    config = Config(
        timezone="America/Sao_Paulo",
        amount_initial=Decimal("2.00"),
        amount_gale1=Decimal("5.00"),
        amount_gale2=Decimal("8.00"),
    )
    notifier = TelegramDMNotifier(tg_client=fake, config=config)
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="initial",
        pnl=Decimal("-2.00"),
        cumulative_pnl=Decimal("-2.00"),
        next_stage="gale1",
    )
    # Must show $5.00, not the hardcoded $4.00 default
    assert "$5.00" in fake.sent[0]
    assert "$4.00" not in fake.sent[0]


@pytest.mark.asyncio
async def test_signal_expired_initial() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="initial", trigger_hhmm="10:20")
    expected = (
        "⏰ Signal EXPIRED (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "Trigger was: 10:20 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: no trades placed; signal invalid"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_signal_expired_gale1() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="gale1", trigger_hhmm="10:25")
    expected = (
        "⏰ Signal EXPIRED (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "Gale1 trigger was: 10:25 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: no gale2 placed — cascade ended"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_signal_expired_gale2() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="gale2", trigger_hhmm="10:30")
    expected = (
        "⏰ Signal EXPIRED (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "Gale2 trigger was: 10:30 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: cascade ended, no recovery attempted"
    )
    assert fake.sent[0] == expected
