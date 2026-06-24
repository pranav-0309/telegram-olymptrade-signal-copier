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


def test_default_olymp_account_group_is_demo() -> None:
    assert _config().olymp_account_group == "demo"


def test_default_timezone_is_sao_paulo() -> None:
    assert _config().timezone == "America/Sao_Paulo"


# --- TZ validation --------------------------------------------------------


def test_valid_timezone_passes() -> None:
    cfg = _config(timezone="UTC")
    assert cfg.tz().key == "UTC"


def test_invalid_timezone_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(timezone="Mars/Olympus_Mons")
    assert "unknown timezone" in str(exc_info.value)


# --- Account group validation --------------------------------------------


def test_account_group_real_with_dry_run_true_is_allowed() -> None:
    cfg = _config(olymp_account_group="real", dry_run=True)
    assert cfg.olymp_account_group == "real"


def test_account_group_real_with_dry_run_false_refuses_to_start() -> None:
    """FR-6.6: real account + dry_run off → app refuses to start."""
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="real", dry_run=False)
    msg = str(exc_info.value)
    assert "Refusing to start" in msg
    assert "DRY_RUN=true" in msg


def test_account_group_demo_with_dry_run_false_is_allowed() -> None:
    cfg = _config(olymp_account_group="demo", dry_run=False)
    assert cfg.olymp_account_group == "demo"


def test_account_group_invalid_value_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="sandbox")
    assert "must be 'demo' or 'real'" in str(exc_info.value)
