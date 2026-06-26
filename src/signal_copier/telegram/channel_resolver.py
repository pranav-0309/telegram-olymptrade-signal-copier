"""Resolve a Telegram chat_id by scanning the user's dialog list for a
channel whose title matches a configurable pattern. Defensively re-verifies
the title on every incoming event so that channel renames mid-session are
detected."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


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

    def _normalize(self, s: str) -> str:
        """Lowercase + collapse whitespace + strip. Symmetric: same
        normalization is applied to pattern and to candidate titles."""
        return " ".join(s.lower().split())
