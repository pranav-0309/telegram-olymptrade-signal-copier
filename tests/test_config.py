from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from signal_copier.config import Config


def _config(**overrides: Any) -> Config:
    # `_env_file=None` is a Pydantic private API used to skip .env loading in tests.
    # The cast on init_kwargs keeps mypy strict-mode happy without changing runtime behavior.
    init_kwargs: Any = {"_env_file": None, **overrides}
    return Config(**init_kwargs)


# --- Defaults -------------------------------------------------------------


def test_default_amount_initial_is_2_00() -> None:
    assert _config().amount_initial == Decimal("2.00")


def test_default_amount_gale1_is_4_00() -> None:
    assert _config().amount_gale1 == Decimal("4.00")


def test_default_amount_gale2_is_8_00() -> None:
    assert _config().amount_gale2 == Decimal("8.00")


def test_default_dry_run_is_true() -> None:
    assert _config().dry_run is True


def test_default_mt5_server_is_empty() -> None:
    """M13.1: empty default allows tests/.env files with no MT5_* to load.

    The runtime guard at __main__.py:49-56 catches missing creds when
    DRY_RUN=false; the validator's allow-empty short-circuit keeps
    pytest green until then.
    """
    assert _config().mt5_server == ""


def test_mt5_server_with_demo_substring_is_allowed() -> None:
    cfg = _config(mt5_server="VTMarkets-Demo")
    assert cfg.mt5_server == "VTMarkets-Demo"


def test_mt5_server_demo_substring_is_case_insensitive() -> None:
    cfg = _config(mt5_server="vtmarkets-DEMO")
    assert cfg.mt5_server == "vtmarkets-DEMO"


def test_mt5_server_non_demo_refuses() -> None:
    """FR-6.6 equivalent for MT5 (docs/refactor.md §4.6)."""
    with pytest.raises(ValidationError) as exc_info:
        _config(mt5_server="VTMarkets-Real01")
    assert "must contain 'demo'" in str(exc_info.value)


def test_mt5_server_demo_with_dry_run_false_is_allowed() -> None:
    """A demo server + DRY_RUN=false is the M13.2 deployment shape."""
    cfg = _config(mt5_server="VTMarkets-Demo", dry_run=False)
    assert cfg.mt5_server == "VTMarkets-Demo"
    assert cfg.dry_run is False
