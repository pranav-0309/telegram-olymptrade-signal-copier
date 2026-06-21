from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_copier.telegram.client import (
    TelegramClient,
    TelegramConfigError,
    compute_backoff_seconds,
)

# --- compute_backoff_seconds (pure function) ------------------------------


def test_compute_backoff_seconds_exponential() -> None:
    assert compute_backoff_seconds(0) == 1.0
    assert compute_backoff_seconds(1) == 2.0
    assert compute_backoff_seconds(2) == 4.0
    assert compute_backoff_seconds(3) == 8.0
    assert compute_backoff_seconds(4) == 16.0


def test_compute_backoff_seconds_capped_at_30() -> None:
    assert compute_backoff_seconds(5) == 30.0
    assert compute_backoff_seconds(6) == 30.0
    assert compute_backoff_seconds(20) == 30.0


def test_compute_backoff_seconds_returns_float() -> None:
    assert isinstance(compute_backoff_seconds(0), float)
    assert isinstance(compute_backoff_seconds(5), float)


# --- TelegramClient.__init__ validation ------------------------------------


def test_init_raises_on_zero_api_id() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_API_ID"):
        TelegramClient(
            api_id=0,
            api_hash="abc",
            phone="+1",
            session_string="s",
            target_chat="@c",
        )


def test_init_raises_on_empty_api_hash() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_API_HASH"):
        TelegramClient(
            api_id=1,
            api_hash="",
            phone="+1",
            session_string="s",
            target_chat="@c",
        )


def test_init_raises_on_empty_phone() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_PHONE"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="",
            session_string="s",
            target_chat="@c",
        )


def test_init_raises_on_empty_session_string() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_SESSION_STRING"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="",
            target_chat="@c",
        )


def test_init_raises_helpful_message_for_empty_session() -> None:
    with pytest.raises(TelegramConfigError, match="telegram.auth"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="",
            target_chat="@c",
        )


# --- TelegramClient.target_chat_id property -------------------------------


def test_target_chat_id_raises_before_connect() -> None:
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="@c",
    )
    with pytest.raises(RuntimeError, match="connect"):
        _ = client.target_chat_id


# --- TelegramClient.add_message_handler requires connect -------------------


def test_add_message_handler_requires_connect() -> None:
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="@c",
    )
    with pytest.raises(RuntimeError, match="connect"):
        client.add_message_handler(handler=AsyncMock())


# --- TelegramClient.close is idempotent -----------------------------------


async def test_close_is_idempotent_when_not_connected() -> None:
    # If close() is called before connect() (or twice after connect()),
    # it should silently no-op rather than raise.
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="@c",
    )
    await client.close()  # before connect
    await client.close()  # again — no-op


# --- TelegramClient.send_to_self ------------------------------------------


async def test_send_to_self_calls_send_message_with_me() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    # Inject a fake underlying Telethon client (bypass real connect()).
    fake_telethon = MagicMock()
    fake_telethon.send_message = AsyncMock()
    client._client = fake_telethon  # type: ignore[attr-defined]

    await client.send_to_self("hello")

    fake_telethon.send_message.assert_awaited_once_with("me", "hello")


async def test_send_to_self_raises_before_connect() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    with pytest.raises(RuntimeError, match="connect"):
        # send_to_self is async, so the body (and its RuntimeError) only
        # runs when awaited — unlike the sync add_message_handler check.
        await client.send_to_self("hello")
