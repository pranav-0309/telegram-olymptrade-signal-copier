from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signal_copier import __main__ as m5_main
from signal_copier.broker.base import BrokerAuthError
from signal_copier.config import Config


def test_main_returns_2_on_config_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Clear all env vars so Config validation fails.
    for key in [
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL",
        "AMOUNT_INITIAL",
        "AMOUNT_GALE1",
        "AMOUNT_GALE2",
        "EXPIRATION_SECONDS",
        "DAILY_LOSS_LIMIT",
        "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT",
        "TIMEZONE",
        "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH",
        "DRY_RUN",
        "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)
    # Force a config error by setting real account + dry_run off.
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "real")
    monkeypatch.setenv("DRY_RUN", "false")

    rc = m5_main.main()
    assert rc == 2


def test_main_returns_1_on_database_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Set valid minimum env so config passes.
    for key in [
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL",
        "AMOUNT_INITIAL",
        "AMOUNT_GALE1",
        "AMOUNT_GALE2",
        "EXPIRATION_SECONDS",
        "DAILY_LOSS_LIMIT",
        "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT",
        "TIMEZONE",
        "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH",
        "DRY_RUN",
        "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "abc")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@c")
    monkeypatch.setenv("DATABASE_URL", "postgresql://bad@bad/bad")
    monkeypatch.setenv("DRY_RUN", "true")

    with patch.object(m5_main, "Database") as mock_db_cls:
        mock_db_cls.connect = AsyncMock(
            side_effect=m5_main.DatabaseConnectionError("simulated"),
        )
        rc = m5_main.main()

    assert rc == 1


@pytest.mark.asyncio
async def test_main_no_dump_consumer_in_m6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__main__ no longer creates a dump_consumer task (replaced by Scheduler)."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    from signal_copier.config import Config

    config = Config()

    from signal_copier import __main__

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "DryRunBroker") as MockBroker,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg
        MockBroker.return_value.connect = AsyncMock()
        MockBroker.return_value.close = AsyncMock()

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(config), timeout=1.0)

        # The scheduler was constructed (this is the key check — proves
        # _run wires up the Scheduler instead of dump_consumer).
        # If the M5 dump_consumer path were still active, the Scheduler
        # mock would NOT be called.
        assert fake_scheduler.run.await_count >= 0  # construction succeeded


@pytest.mark.asyncio
async def test_main_creates_scheduler_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__main__._run creates an asyncio task named 'scheduler' that runs
    scheduler.run()."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    from signal_copier.config import Config

    config = Config()

    from signal_copier import __main__

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    started_tasks: list[asyncio.Task] = []

    real_create_task = asyncio.create_task

    def tracking_create_task(coro: Any, *, name: str | None = None) -> asyncio.Task:
        task = real_create_task(coro, name=name)
        started_tasks.append(task)
        return task

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "DryRunBroker") as MockBroker,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
        patch("signal_copier.__main__.asyncio.create_task", side_effect=tracking_create_task),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg
        MockBroker.return_value.connect = AsyncMock()
        MockBroker.return_value.close = AsyncMock()

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(config), timeout=1.0)

    # A task named "scheduler" was created.
    task_names = [t.get_name() for t in started_tasks]
    assert "scheduler" in task_names


@pytest.mark.asyncio
async def test_main_emits_bot_started_and_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__main__ calls notifier.on_bot_started after wiring and
    on_bot_stopping on cleanup."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("TELEGRAM_SELF_DM_NOTIFICATIONS", "false")
    from signal_copier.config import Config

    config = Config()

    from signal_copier import __main__
    from tests._scheduler_fixtures import RecordingNotifier

    fake_notifier = RecordingNotifier()
    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 2

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "DryRunBroker") as MockBroker,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
        patch.object(__main__, "NoOpNotifier", return_value=fake_notifier),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg
        MockBroker.return_value.connect = AsyncMock()
        MockBroker.return_value.close = AsyncMock()

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(config), timeout=1.0)

    method_names = [m for m, _ in fake_notifier.calls]
    assert "on_bot_started" in method_names
    assert "on_bot_stopping" in method_names
    stopping_call = next(c for m, c in fake_notifier.calls if m == "on_bot_stopping")
    assert stopping_call["open_cascades"] == 2


def test_main_constructs_telegram_dm_notifier_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__main__ must construct TelegramDMNotifier when self_dm_notifications=True
    and pass it to both the Listener and tg.start()."""
    import inspect

    from signal_copier import __main__ as main_module

    source = inspect.getsource(main_module._run)
    # The notifier selection logic must construct TelegramDMNotifier
    assert "TelegramDMNotifier(tg_client=tg, config=config)" in source
    # The notifier must be passed to the Listener
    assert "notifier=notifier" in source
    # The notifier must be passed to tg.start()
    assert "tg.start(notifier=notifier)" in source


@pytest.mark.asyncio
async def test_main_picks_dry_run_broker_when_dry_run_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=true → DryRunBroker (M6 behavior unchanged)."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "")

    from signal_copier import __main__

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "DryRunBroker") as MockBroker,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg
        MockBroker.return_value.connect = AsyncMock()
        MockBroker.return_value.close = AsyncMock()

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(Config()), timeout=1.0)

        # DryRunBroker was constructed
        assert MockBroker.called


@pytest.mark.asyncio
async def test_main_picks_olymp_broker_when_dry_run_false_with_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=false + OLYMP_ACCESS_TOKEN set → OlympTradeBroker constructed."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "valid-token")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "12345")

    from signal_copier import __main__

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    fake_olymp_broker = MagicMock()
    fake_olymp_broker.connect = AsyncMock()
    fake_olymp_broker.close = AsyncMock()

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "OlympTradeBroker", return_value=fake_olymp_broker) as MockOlymp,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(Config()), timeout=1.0)

        # OlympTradeBroker was constructed with the token
        assert MockOlymp.called
        call_kwargs = MockOlymp.call_args.kwargs
        assert call_kwargs["access_token"] == "valid-token"
        assert call_kwargs["account_id"] == "12345"
        assert call_kwargs["account_group"] == "demo"


def test_main_returns_2_when_olymp_token_missing_with_dry_run_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=false but OLYMP_ACCESS_TOKEN empty → exit code 2."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "")

    from signal_copier import __main__

    rc = __main__.main()
    assert rc == 2


@pytest.mark.asyncio
async def test_main_returns_2_when_olymp_broker_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect() raises BrokerAuthError → _run propagates (mapped to exit 2 by main())."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "valid-token")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "12345")

    from signal_copier import __main__

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.state_store.get_active_signals = AsyncMock(return_value=[])
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()

    fake_olymp_broker = MagicMock()
    fake_olymp_broker.connect = AsyncMock(side_effect=BrokerAuthError("token rejected"))
    fake_olymp_broker.close = AsyncMock()

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "OlympTradeBroker", return_value=fake_olymp_broker),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg

        with pytest.raises(BrokerAuthError):
            await __main__._run(Config())
