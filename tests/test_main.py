from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signal_copier import __main__ as m5_main


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
        mock_db = MagicMock()
        mock_db.connect = AsyncMock(
            side_effect=m5_main.DatabaseConnectionError("simulated"),
        )
        mock_db_cls.connect = AsyncMock(return_value=mock_db)

        rc = m5_main.main()

    assert rc == 1
