from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from signal_copier.telegram.channel_resolver import (
    ChannelAmbiguousError,
    ChannelNotFoundError,
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


def test_init_preserves_raw_pattern_unchanged() -> None:
    """Pattern is stored verbatim while _normalized_pattern is normalized."""
    resolver = ChannelResolver(pattern="MagicTrader")
    assert resolver._pattern == "MagicTrader"
    assert resolver._normalized_pattern == "magictrader"


# --- resolve() tests -----------------------------------------------------


async def test_resolve_returns_chat_id_when_one_match() -> None:
    """Mock get_dialogs() returns 5 dialogs incl. one matching → returns its
    ID and stores the exact title."""
    dialogs = [
        _FakeDialog(id=1, title="Random Group"),
        _FakeDialog(id=2, title="Magic Trader Signals 🚀"),
        _FakeDialog(id=3, title="Family Chat"),
        _FakeDialog(id=4, title="Work Team"),
        _FakeDialog(id=5, title=None),  # edge: no title
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 2
    assert resolver.resolved_chat_id == 2
    assert resolver.captured_title == "Magic Trader Signals 🚀"
    assert client.get_dialogs_calls == 1


async def test_resolve_raises_ChannelNotFoundError_on_zero_matches() -> None:
    """Empty dialog list → ChannelNotFoundError mentioning pattern."""
    client = _FakeTelethonClient(dialogs=[])
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(ChannelNotFoundError, match="Magic Trader Signals"):
        await resolver.resolve(client)  # type: ignore[arg-type]


async def test_resolve_raises_ChannelNotFoundError_with_scanned_count() -> None:
    """Error message includes scanned dialog count for actionable diagnostics."""
    dialogs = [
        _FakeDialog(id=1, title="Other Channel"),
        _FakeDialog(id=2, title="Another Channel"),
        _FakeDialog(id=3, title="Yet Another"),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(ChannelNotFoundError, match="Scanned 3 dialogs"):
        await resolver.resolve(client)  # type: ignore[arg-type]


async def test_resolve_raises_ChannelAmbiguousError_on_multiple_matches() -> None:
    """2 dialogs match → ChannelAmbiguousError listing both titles."""
    dialogs = [
        _FakeDialog(id=1, title="Magic Patterns Chat"),
        _FakeDialog(id=2, title="Magic Hour"),
        _FakeDialog(id=3, title="Daily News"),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic")

    with pytest.raises(ChannelAmbiguousError) as excinfo:
        await resolver.resolve(client)  # type: ignore[arg-type]
    msg = str(excinfo.value)
    assert "2 dialogs" in msg
    assert "Magic Patterns Chat" in msg
    assert "Magic Hour" in msg


async def test_resolve_is_case_insensitive() -> None:
    """Dialog title 'MAGIC TRADER SIGNALS' matches pattern 'magic trader'."""
    dialogs = [_FakeDialog(id=42, title="MAGIC TRADER SIGNALS")]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="magic trader")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 42


async def test_resolve_ignores_titles_with_none() -> None:
    """Dialog with title=None doesn't crash; just excluded from matches."""
    dialogs = [
        _FakeDialog(id=1, title=None),
        _FakeDialog(id=2, title="Magic Trader Signals"),
        _FakeDialog(id=3, title=None),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 2


async def test_resolve_collapses_whitespace_in_dialog_title() -> None:
    """Dialog title with extra whitespace still matches after normalization."""
    dialogs = [_FakeDialog(id=7, title="Magic   Trader   Signals")]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 7


# --- matches() tests -----------------------------------------------------


async def _resolve_with_id(chat_id: int, title: str) -> ChannelResolver:
    """Helper: build a resolver pre-populated with a resolved chat_id."""
    resolver = ChannelResolver(pattern="Magic Trader Signals")
    dialogs = [_FakeDialog(id=chat_id, title=title)]
    client = _FakeTelethonClient(dialogs=dialogs)
    await resolver.resolve(client)  # type: ignore[arg-type]
    return resolver


def test_matches_chat_id_fast_path() -> None:
    """event.chat_id == resolved AND title matches → True."""
    import asyncio

    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is True


def test_matches_rejects_wrong_chat_id() -> None:
    """event.chat_id != resolved → False (fast-path, no title check)."""
    import asyncio

    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=99, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is False


def test_matches_rejects_chat_id_match_but_title_drift() -> None:
    """event.chat_id == resolved but title drifted (rename) → False."""
    import asyncio

    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title="Totally Different Channel")

    assert resolver.matches(event) is False


def test_matches_accepts_when_chat_object_unavailable() -> None:
    """event.chat_id == resolved but event.chat is None → True + WARNING logged.
    Edge case: Telethon occasionally delivers events without chat metadata."""
    import asyncio

    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title=None)  # sets event.chat = None

    assert resolver.matches(event) is True


def test_matches_rejects_when_title_is_none() -> None:
    """event.chat_id == resolved but event.chat.title is None → False."""
    import asyncio

    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = MagicMock()
    event.chat_id = 42
    chat = MagicMock()
    chat.title = None
    event.chat = chat

    assert resolver.matches(event) is False


def test_matches_uses_normalized_comparison() -> None:
    """Pattern with extra whitespace matches title without extra whitespace."""
    import asyncio

    resolver = ChannelResolver(pattern="  MAGIC  trader  SIGNALS  ")
    dialogs = [_FakeDialog(id=42, title="Magic Trader Signals")]
    client = _FakeTelethonClient(dialogs=dialogs)
    asyncio.run(resolver.resolve(client))  # type: ignore[arg-type]

    event = _make_event(chat_id=42, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is True
