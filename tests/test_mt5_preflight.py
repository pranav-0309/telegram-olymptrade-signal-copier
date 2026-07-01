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
