from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from signal_copier.config import Config
from signal_copier.domain.gale import amount_for_stage, compute_gale_triggers


def _config(**overrides: Any) -> Config:
    # `_env_file=None` is a Pydantic private API used to skip .env loading in tests.
    # The cast on init_kwargs keeps mypy strict-mode happy without changing runtime behavior.
    init_kwargs: Any = {"_env_file": None, **overrides}
    return Config(**init_kwargs)


# --- amount_for_stage -----------------------------------------------------


def test_amount_for_initial_stage_returns_configured_value() -> None:
    cfg = _config(amount_initial=Decimal("2.00"))
    assert amount_for_stage("initial", cfg) == Decimal("2.00")


def test_amount_for_gale1_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale1=Decimal("4.00"))
    assert amount_for_stage("gale1", cfg) == Decimal("4.00")


def test_amount_for_gale2_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale2=Decimal("8.00"))
    assert amount_for_stage("gale2", cfg) == Decimal("8.00")


def test_amount_for_stage_returns_decimal_not_float() -> None:
    cfg = _config()
    result = amount_for_stage("initial", cfg)
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    ("initial", "gale1", "gale2"),
    [
        (Decimal("2.00"), Decimal("4.00"), Decimal("8.00")),  # v1 default
        (Decimal("1.00"), Decimal("2.00"), Decimal("3.00")),  # non-default
        (Decimal("5.50"), Decimal("11.00"), Decimal("22.00")),  # arbitrary
    ],
)
def test_amount_for_stage_reads_from_config(
    initial: Decimal, gale1: Decimal, gale2: Decimal
) -> None:
    cfg = _config(amount_initial=initial, amount_gale1=gale1, amount_gale2=gale2)
    assert amount_for_stage("initial", cfg) == initial
    assert amount_for_stage("gale1", cfg) == gale1
    assert amount_for_stage("gale2", cfg) == gale2


# --- compute_gale_triggers -----------------------------------------------


def test_compute_gale_triggers_for_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 300)
    assert gale1 == initial_unix + 300.0
    assert gale2 == initial_unix + 600.0


def test_compute_gale_triggers_for_non_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 60)  # 1-min expiration
    assert gale1 == initial_unix + 60.0
    assert gale2 == initial_unix + 120.0


def test_compute_gale_triggers_returns_floats() -> None:
    gale1, gale2 = compute_gale_triggers(1_700_000_000.0, 300)
    assert isinstance(gale1, float)
    assert isinstance(gale2, float)


def test_compute_gale_triggers_handles_zero_initial() -> None:
    gale1, gale2 = compute_gale_triggers(0.0, 300)
    assert gale1 == 300.0
    assert gale2 == 600.0
