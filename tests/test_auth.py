from __future__ import annotations

from unittest.mock import patch

import pytest

from signal_copier.telegram import auth


def test_read_creds_succeeds_with_full_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    # Clean up other env vars that Config might read from previous tests.
    for key in [
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

    api_id, api_hash, phone = auth._read_creds()
    assert api_id == 12345
    assert api_hash == "abc123"
    assert phone == "+15551234567"


def test_main_returns_2_on_missing_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Clear all env vars that Config reads.
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

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "TELEGRAM_API_ID" in err or "Config validation" in err


def test_main_returns_2_on_zero_api_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "0")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1")
    # Clear session string.
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "TELEGRAM_API_ID" in err


def test_main_returns_1_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in [
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    async def _failing_auth_and_verify(*args: object, **kwargs: object) -> tuple[str, object]:
        raise RuntimeError("simulated auth failure")

    with patch.object(auth, "_do_auth_and_verify", side_effect=_failing_auth_and_verify):
        rc = auth.main()

    assert rc == 1
    err = capsys.readouterr().err
    assert "auth or verify failed" in err.lower()
    assert "simulated auth failure" in err


def test_main_prints_session_string_on_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key in [
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    SESSION = "AAAAfakebase64session=="
    fake_user = type(
        "User",
        (),
        {
            "first_name": "Bob",
            "last_name": "Builder",
            "username": "bobbuilds",
            "id": 111222333,
        },
    )()

    async def _success_auth_and_verify(*args: object, **kwargs: object) -> tuple[str, object]:
        return SESSION, fake_user

    with patch.object(auth, "_do_auth_and_verify", side_effect=_success_auth_and_verify):
        rc = auth.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert f"TELEGRAM_SESSION_STRING={SESSION}" in out
    assert "Bob Builder" in out
    assert "@bobbuilds" in out
    assert "111222333" in out
    assert "Treat the session string like a password" in out


def test_main_refuses_to_run_on_railway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per spec §5.3 step 2: the helper must refuse to run on Railway.

    Detected by RAILWAY_ENVIRONMENT or RAILWAY_PROJECT_ID env vars.
    Exits with code 2 and prints a one-line instruction.
    """
    # Set valid creds so the env-var check would otherwise pass.
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    # Simulate Railway.
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    # Clean up other env vars that Config might read from previous tests.
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
    ]:
        monkeypatch.delenv(key, raising=False)

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "locally" in err.lower()
    assert "railway" in err.lower()


def test_main_verifies_session_and_prints_rich_banner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per spec §5.3 step 7 + step 9: the helper must verify the session
    via get_me() and print a rich banner with user info + security warning.
    The combined _do_auth_and_verify coroutine returns (session_str, user).
    """
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    SESSION = "AAAAfakebase64session=="
    fake_user = type(
        "User",
        (),
        {
            "first_name": "Alice",
            "last_name": "Tester",
            "username": "alicehandle",
            "id": 987654321,
        },
    )()

    async def _success_auth_and_verify(*args: object, **kwargs: object) -> tuple[str, object]:
        return SESSION, fake_user

    with patch.object(auth, "_do_auth_and_verify", side_effect=_success_auth_and_verify):
        rc = auth.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Alice Tester" in out
    assert "@alicehandle" in out
    assert "987654321" in out
    assert f"TELEGRAM_SESSION_STRING={SESSION}" in out
    assert "Treat the session string like a password" in out
