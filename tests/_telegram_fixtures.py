"""Shared test fixtures for the M5 telegram module.

Helpers:
  - make_event: build a synthetic Telethon NewMessage.Event for tests.
  - FakeStateStore: drop-in replacement for StateStore that records
    upsert_signal calls and returns a configurable bool.
  - NullLogger: a logging.Logger that swallows records; lets tests
    assert on parse-failure routing without writing to logs/parse_failures.log.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from signal_copier.domain.signal import Signal


class _StubMessage:
    """Minimal stand-in for telethon.tl.custom.message.Message."""

    def __init__(self, *, message_id: int, outgoing: bool = False) -> None:
        self.id = message_id
        self.out = outgoing


def make_event(
    *,
    text: str,
    chat_id: int,
    message_id: int = 1,
    outgoing: bool = False,
) -> Any:
    """Build a synthetic Telethon NewMessage.Event.

    Only the attributes Listener reads are populated. Tests can call
    listener.on_new_message(make_event(...)) and assert on the side
    effects (queue contents, upsert_signal calls, parse_failures logs).
    """
    event = MagicMock()
    event.text = text
    event.chat_id = chat_id
    event.message = _StubMessage(message_id=message_id, outgoing=outgoing)
    return event


class FakeStateStore:
    """Drop-in replacement for StateStore. Records upsert_signal calls."""

    def __init__(self, *, next_insert_returns: bool = True) -> None:
        self.upserted: list[Signal] = []
        self._next_returns = next_insert_returns

    async def upsert_signal(self, signal: Signal) -> bool:
        self.upserted.append(signal)
        return self._next_returns


class NullLogger(logging.Logger):
    """A logging.Logger that swallows all records.

    Used in tests that don't care about parse-failure logging.
    """

    def __init__(self, name: str = "null") -> None:
        super().__init__(name, level=logging.CRITICAL + 1)

    def handle(self, record: logging.LogRecord) -> None:
        return None
