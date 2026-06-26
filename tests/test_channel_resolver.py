from __future__ import annotations

from unittest.mock import MagicMock

from signal_copier.telegram.channel_resolver import (
    ChannelResolver,
)

# --- Test fixtures --------------------------------------------------------


class _FakeDialog:
    """Minimal stand-in for a Telethon Dialog (only the fields we read)."""

    def __init__(self, *, id: int, title: str | None = None) -> None:
        self.id = id
        self.title = title


class _FakeTelethonClient:
    """Minimal stand-in for a Telethon TelegramClient — just get_dialogs()."""

    def __init__(self, dialogs: list[_FakeDialog] | None = None) -> None:
        self._dialogs = dialogs or []
        self.get_dialogs_calls: int = 0
        self.raise_on_get_dialogs: BaseException | None = None

    async def get_dialogs(self) -> list[_FakeDialog]:
        self.get_dialogs_calls += 1
        if self.raise_on_get_dialogs is not None:
            raise self.raise_on_get_dialogs
        return self._dialogs


def _make_event(
    *,
    chat_id: int,
    chat_title: str | None = "Some Channel",
    message_id: int = 1,
    outgoing: bool = False,
) -> MagicMock:
    """Build a MagicMock Telethon NewMessage.Event."""
    event = MagicMock()
    event.chat_id = chat_id
    if chat_title is None:
        event.chat = None
    else:
        event.chat.title = chat_title
    event.message.id = message_id
    event.message.out = outgoing
    event.text = "ignored by ChannelResolver"
    return event


# --- Tests ---------------------------------------------------------------


def test_init_normalizes_pattern() -> None:
    """Pattern is lowercased + whitespace-collapsed in _normalized_pattern;
    raw pattern is preserved for error messages."""
    resolver = ChannelResolver(pattern="  Magic  Trader  Signals  ")
    assert resolver._normalized_pattern == "magic trader signals"
    assert resolver._pattern == "  Magic  Trader  Signals  "


def test_init_preserves_empty_pattern_for_error_messages() -> None:
    """Raw pattern is preserved verbatim — useful in error messages."""
    resolver = ChannelResolver(pattern="MagicTrader")
    assert resolver._pattern == "MagicTrader"
    assert resolver._normalized_pattern == "magictrader"
