"""Resolve a Telegram chat_id by scanning the user's dialog list for a
channel whose title matches a configurable pattern. Defensively re-verifies
the title on every incoming event so that channel renames mid-session are
detected."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from telethon import TelegramClient as _TelethonClient


class ChannelNotFoundError(RuntimeError):
    """Raised when zero dialogs match the configured title pattern."""


class ChannelAmbiguousError(RuntimeError):
    """Raised when more than one dialog matches the configured title pattern."""


class ChannelResolver:
    """Scan the user's dialog list for a channel whose title contains the
    configured pattern (case-insensitive substring, whitespace-normalized).
    Fail fast on 0 or >1 matches. Defensively re-verify title on every event.
    """

    def __init__(self, *, pattern: str) -> None:
        self._pattern: str = pattern
        self._normalized_pattern: str = self._normalize(pattern)
        self._resolved_chat_id: int | None = None
        self._captured_title: str | None = None

    @property
    def resolved_chat_id(self) -> int:
        """Raises RuntimeError if resolve() has not been called yet."""
        if self._resolved_chat_id is None:
            raise RuntimeError(
                "ChannelResolver.resolve() has not been called yet; "
                "no chat_id has been resolved."
            )
        return self._resolved_chat_id

    @property
    def captured_title(self) -> str:
        """The exact title captured at startup. Used for diagnostics."""
        if self._captured_title is None:
            raise RuntimeError(
                "ChannelResolver.resolve() has not been called yet; " "no title has been captured."
            )
        return self._captured_title

    async def resolve(self, client: _TelethonClient) -> int:
        """Scan the user's dialog list for a title match. Fail fast on
        0 or >1 matches. Returns the resolved chat_id and caches it."""
        dialogs = await client.get_dialogs()
        matches = [
            d for d in dialogs if d.title and self._normalized_pattern in self._normalize(d.title)
        ]
        if len(matches) == 0:
            raise ChannelNotFoundError(
                f"No Telegram dialog matches pattern {self._pattern!r}. "
                f"Scanned {len(dialogs)} dialogs. "
                f"Check TELEGRAM_TARGET_CHAT in .env."
            )
        if len(matches) > 1:
            titles = [m.title for m in matches]
            raise ChannelAmbiguousError(
                f"{len(matches)} dialogs match pattern {self._pattern!r}: "
                f"{titles}. Make the pattern more specific."
            )
        match = matches[0]
        self._resolved_chat_id = match.id
        self._captured_title = match.title
        _log.info(
            "ChannelResolver resolved pattern=%r → chat_id=%d (title=%r)",
            self._pattern,
            match.id,
            match.title,
        )
        return match.id  # type: ignore[no-any-return]

    def _normalize(self, s: str) -> str:
        """Lowercase + collapse whitespace + strip. Symmetric: same
        normalization is applied to pattern and to candidate titles."""
        return " ".join(s.lower().split())

    def matches(self, event: Any) -> bool:
        """Per-event filter. Returns True iff:
          (a) event.chat_id == self._resolved_chat_id, AND
          (b) event.chat is not None, AND
          (c) self._normalized_pattern is a substring of the normalized title.

        If chat_id matches but event.chat is unavailable (rare Telethon
        edge case for very new chats), accepts on chat_id alone + WARNING.

        Cheap: one int compare + (usually) one lowercase string contains.
        """
        if self._resolved_chat_id is None:
            raise RuntimeError(
                "ChannelResolver.matches() called before resolve(); "
                "no chat_id has been resolved."
            )

        # Fast-path: chat_id mismatch → skip title work entirely
        if event.chat_id != self._resolved_chat_id:
            return False

        # chat_id matched. If event.chat is unavailable, accept defensively.
        chat = getattr(event, "chat", None)
        if chat is None:
            _log.warning(
                "chat_id=%d matched but event.chat unavailable; " "accepting on chat_id alone",
                self._resolved_chat_id,
            )
            return True

        title = getattr(chat, "title", None)
        if title is None:
            _log.warning(
                "chat_id=%d matched but chat has no title; ignoring",
                self._resolved_chat_id,
            )
            return False

        return self._normalized_pattern in self._normalize(title)
