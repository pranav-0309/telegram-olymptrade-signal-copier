from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from telethon import TelegramClient as TelethonClient
from telethon.errors import FloodWaitError
from telethon.events import MessageEdited, NewMessage
from telethon.sessions import StringSession

if TYPE_CHECKING:
    from signal_copier.notify.protocol import Notifier

_log = logging.getLogger(__name__)


_MAX_RECONNECT_ATTEMPTS: int = 10
_BACKOFF_BASE_SECONDS: float = 1.0
_BACKOFF_CAP_SECONDS: float = 30.0
_FLOOD_WAIT_THRESHOLD_SECONDS: int = 60


class TelegramConfigError(RuntimeError):
    """Raised when required config is missing/invalid or the target chat
    cannot be resolved at startup. Caught by __main__; exits 2."""


def compute_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a 30s cap. attempt is 0-indexed.

    attempt=0 -> 1.0, attempt=1 -> 2.0, ..., attempt=4 -> 16.0,
    attempt>=5 -> 30.0 (capped).
    """
    return min(_BACKOFF_BASE_SECONDS * (2.0**attempt), _BACKOFF_CAP_SECONDS)


class TelegramClient:
    """Thin wrapper over the vendored Telethon client.

    Owns the StringSession lifecycle, the reconnect supervisor, and
    the FloodWaitError policy. Construction is sync (validates config
    eagerly — D-12). connect() opens the MTProto connection; ChannelResolver resolves the target
    chat. start() runs the client until disconnect with exponential-backoff
    reconnect.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
        target_chat: str,
    ) -> None:
        if api_id == 0:
            raise TelegramConfigError(
                "TELEGRAM_API_ID is 0; set it in .env (get from my.telegram.org)"
            )
        if not api_hash:
            raise TelegramConfigError("TELEGRAM_API_HASH is empty; set it in .env")
        if not phone:
            raise TelegramConfigError("TELEGRAM_PHONE is empty; set it in .env")
        if not session_string:
            raise TelegramConfigError(
                "TELEGRAM_SESSION_STRING is empty; run "
                "'python -m signal_copier.telegram.auth' to generate one"
            )
        if not target_chat:
            raise TelegramConfigError(
                "TELEGRAM_TARGET_CHAT is empty; set it in .env to the channel "
                "title pattern (e.g. 'Magic Trader Signals')"
            )

        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._target_chat = target_chat
        self._session_string = session_string

        self._client: TelethonClient | None = None
        self._target_chat_id: int | None = None

    @property
    def target_chat_id(self) -> int:
        if self._target_chat_id is None:
            raise RuntimeError(
                "target_chat_id is not resolved; call TelegramClient.connect() first"
            )
        return self._target_chat_id

    @property
    def raw_client(self) -> TelethonClient:
        """The underlying Telethon client. Escape hatch for ChannelResolver
        so it can call get_dialogs(). All other components should use
        TelegramClient's own API."""
        if self._client is None:
            raise RuntimeError(
                "raw_client accessed before connect(); call TelegramClient.connect() first"
            )
        return self._client

    def set_resolved_chat_id(self, chat_id: int) -> None:
        """Externally inject the resolved chat_id (typically from
        ChannelResolver.resolve()). Required because connect() no longer
        resolves the chat itself."""
        self._target_chat_id = chat_id

    async def connect(self) -> None:
        """Authenticate and open the MTProto connection. Does NOT resolve
        the target chat — that is ChannelResolver's responsibility."""
        self._client = TelethonClient(
            StringSession(self._session_string),
            self._api_id,
            self._api_hash,
        )
        await self._client.connect()
        _log.info(
            "TelegramClient connected (target_chat_pattern=%r)",
            self._target_chat,
        )

    def add_message_handler(
        self,
        handler: Callable[[Any], Awaitable[None]],
    ) -> None:
        if self._client is None:
            raise RuntimeError(
                "add_message_handler called before connect(); call TelegramClient.connect() first"
            )
        self._client.on(NewMessage)(handler)
        self._client.on(MessageEdited)(handler)

    async def start(self, *, notifier: Notifier | None = None) -> None:
        if self._client is None:
            raise RuntimeError("start() called before connect()")
        attempt = 0
        while True:
            try:
                await self._client.run_until_disconnected()
                return
            except FloodWaitError as exc:
                if exc.seconds > _FLOOD_WAIT_THRESHOLD_SECONDS:
                    _log.error(
                        "Telegram FloodWaitError: %ds wait requested; re-raising "
                        "(FR-1.7: 'raise + log for longer')",
                        exc.seconds,
                    )
                    raise
                _log.warning("FloodWaitError %ds; continuing", exc.seconds)
                continue
            except ConnectionError as exc:
                attempt += 1
                if attempt > _MAX_RECONNECT_ATTEMPTS:
                    _log.error(
                        "Telegram reconnect failed after %d attempts; re-raising",
                        _MAX_RECONNECT_ATTEMPTS,
                    )
                    raise
                delay = compute_backoff_seconds(attempt - 1)
                _log.warning(
                    "Telegram ConnectionError: %s. Reconnect attempt %d/%d in %.1fs",
                    type(exc).__name__,
                    attempt,
                    _MAX_RECONNECT_ATTEMPTS,
                    delay,
                )
                if notifier is not None:
                    await notifier.on_telegram_disconnect()
                await asyncio.sleep(delay)

    async def send_to_self(self, text: str) -> None:
        """Send a Telegram DM to the user's own 'Saved Messages' chat.

        Uses the same connection as the listener (FR-7.4). Plain text
        only — no parse_mode. Raises whatever Telethon's send_message
        raises (FloodWaitError, ConnectionError, OSError); callers
        (TelegramDMNotifier._send) are responsible for handling.
        """
        if self._client is None:
            raise RuntimeError("send_to_self() called before connect()")
        await self._client.send_message("me", text)

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        except Exception as exc:  # noqa: BLE001 — close is best-effort
            _log.debug("TelegramClient.close: disconnect raised: %s", exc)
        self._client = None
        self._target_chat_id = None
