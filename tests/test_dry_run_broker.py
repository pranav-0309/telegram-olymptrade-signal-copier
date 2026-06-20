from __future__ import annotations

import logging

import pytest

from signal_copier.broker.dry_run import DryRunBroker


async def test_connect_logs_and_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.connect()
    await broker.connect()  # second call must not raise


async def test_close_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.close()
    await broker.close()  # second call must not raise


async def test_account_group_logged_on_connect(caplog: pytest.LogCaptureFixture) -> None:
    broker = DryRunBroker(account_group="demo")
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any("account_group=demo" in record.message for record in caplog.records)


async def test_default_account_group_is_demo(caplog: pytest.LogCaptureFixture) -> None:
    # The default constructor argument is "demo" — confirms the field default.
    broker = DryRunBroker()
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any("account_group=demo" in record.message for record in caplog.records)
