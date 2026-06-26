from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_copier.notify.protocol import NoOpNotifier
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
    client._client = fake_telethon

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


# --- TelegramClient.start() with optional notifier -----------------------


async def test_start_emits_on_telegram_disconnect_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When run_until_disconnected() raises ConnectionError, the optional
    notifier's on_telegram_disconnect() must be called BEFORE the backoff
    sleep (so the DM fires as soon as the disconnect is detected)."""
    notifier = NoOpNotifier()
    disconnect_calls: list[None] = []

    # Wrap the NoOpNotifier method to record the call.
    original = notifier.on_telegram_disconnect

    async def recorder() -> None:
        disconnect_calls.append(None)
        await original()

    notifier.on_telegram_disconnect = recorder  # type: ignore[method-assign]

    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    fake_telethon = MagicMock()
    call_count = {"n": 0}

    async def fake_run() -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            client._client = None  # exit the loop
            return
        raise ConnectionError("simulated disconnect")

    fake_telethon.run_until_disconnected = fake_run
    fake_telethon.disconnect = AsyncMock()
    client._client = fake_telethon

    # Patch asyncio.sleep so the test doesn't actually wait.
    sleeps: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await client.start(notifier=notifier)

    assert len(disconnect_calls) == 1, (
        "on_telegram_disconnect must fire exactly once on ConnectionError"
    )
    assert len(sleeps) == 1  # one backoff sleep after the disconnect


# --- TelegramClient.target_chat non-empty check --------------------------


def test_init_raises_on_empty_target_chat() -> None:
    """An empty target_chat must raise TelegramConfigError with an
    actionable message (matches the existing api_id/api_hash/phone/session
    validation pattern)."""
    with pytest.raises(TelegramConfigError, match="TELEGRAM_TARGET_CHAT"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="s",
            target_chat="",
        )


def test_init_raises_helpful_message_for_empty_target_chat() -> None:
    """Error message mentions the channel-title-pattern semantics."""
    with pytest.raises(TelegramConfigError, match="title pattern"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="s",
            target_chat="",
        )


# --- TelegramClient.connect() no longer calls get_entity -----------------


async def test_connect_does_not_call_get_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect() must not attempt to resolve the target chat — that's
    ChannelResolver's job now."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    # Mock the underlying Telethon client; get_entity would raise if called.
    mock_telethon = MagicMock()
    mock_telethon.get_entity = AsyncMock(
        side_effect=RuntimeError("get_entity should not be called by connect()")
    )
    mock_telethon.connect = AsyncMock(return_value=True)
    # Patch the underlying Telethon constructor + StringSession so the
    # fake session_string "abc" doesn't blow up before we even test the
    # behavior we care about (no get_entity call).
    monkeypatch.setattr(
        "signal_copier.telegram.client.StringSession",
        lambda *args, **kwargs: MagicMock(),
    )
    monkeypatch.setattr(
        "signal_copier.telegram.client.TelethonClient",
        lambda *args, **kwargs: mock_telethon,
    )

    await client.connect()  # should NOT raise

    mock_telethon.get_entity.assert_not_called()


# --- TelegramClient.raw_client property -----------------------------------


def test_raw_client_property_returns_underlying() -> None:
    """raw_client returns the underlying Telethon client (escape hatch
    for ChannelResolver)."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    fake_telethon = MagicMock()
    client._client = fake_telethon

    assert client.raw_client is fake_telethon


def test_raw_client_raises_before_connect() -> None:
    """raw_client raises if connect() has not been called."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    with pytest.raises(RuntimeError, match="connect"):
        _ = client.raw_client


# --- TelegramClient.set_resolved_chat_id --------------------------------


def test_set_resolved_chat_id_makes_target_chat_id_accessible() -> None:
    """After set_resolved_chat_id(N), target_chat_id returns N."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )

    with pytest.raises(RuntimeError, match="connect"):
        _ = client.target_chat_id

    client.set_resolved_chat_id(-1001940077808)

    assert client.target_chat_id == -1001940077808
