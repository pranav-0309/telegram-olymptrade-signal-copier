"""Tests for tools.mt5_preflight.

All MT5 calls are mocked via monkeypatch.setattr.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tools.mt5_preflight as preflight


def test_preflight_prints_pass_on_successful_init(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: all 4 steps succeed → exit 0 + PASS in stdout."""
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")
    monkeypatch.delenv("MT5_TERMINAL_PATH", raising=False)

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = True
    fake_mt5.login_info.return_value = ("12345678", "VTMarkets-Demo-STD")
    fake_mt5.account_info.return_value = SimpleNamespace(
        balance=10000.0,
        leverage=500,
        currency="USD",
    )
    fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSD-STD")]

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 0
    captured = capsys.readouterr()
    assert "[OK] mt5.initialize" in captured.out
    assert "[OK] mt5.account_info" in captured.out
    assert "PASS" in captured.out


def test_preflight_exits_1_on_init_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = False
    fake_mt5.last_error.return_value = (-10005, "IPC: No IPC connection")

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 1
    captured = capsys.readouterr()
    assert "[FAIL] mt5.initialize" in captured.out
    assert "IPC: No IPC connection" in captured.out


def test_preflight_exits_1_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)
    # `load_dotenv` runs *inside* run_preflight(); without this patch the test
    # silently passes if a local .env defines any MT5_* vars.
    monkeypatch.setattr(preflight, "load_dotenv", lambda: None)

    rc = preflight.run_preflight()
    assert rc == 1
    captured = capsys.readouterr()
    assert "MT5_LOGIN" in captured.out
    assert "MT5_PASSWORD" in captured.out


def test_preflight_handles_degraded_account_info_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """account_info() returns object with balance=None → preflight doesn't crash, prints WARN."""
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = True
    fake_mt5.login_info.return_value = ("12345678", "VTMarkets-Demo-STD")
    fake_mt5.account_info.return_value = SimpleNamespace(balance=None, leverage=500, currency="USD")
    fake_mt5.symbols_get.return_value = []

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 0
    captured = capsys.readouterr()
    assert "balance=?" in captured.out
