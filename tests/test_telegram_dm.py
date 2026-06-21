"""Tests for signal_copier.notify.telegram_dm — TelegramDMNotifier."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
