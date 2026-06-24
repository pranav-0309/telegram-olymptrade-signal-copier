"""Recorded-session integration test for M8's e:26 parsing.

This test replays a captured e:26 payload through OlympTradeBroker
to catch regressions when the upstream WS protocol shape changes.

Marked `@pytest.mark.slow` so it doesn't run in the default suite.
Run with: `pytest -m slow tests/test_olymp_broker_recorded.py`
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from signal_copier.broker.olymp import OlympTradeBroker, OlympTradeClient
from tests._broker_fixtures import FakeOlympTradeClient
from tests._scheduler_fixtures import RecordingNotifier

FIXTURE = Path(__file__).parent / "fixtures" / "olymp_e26_sample.json"


@pytest.mark.slow
async def test_recorded_e26_message_resolves_correctly() -> None:
    """Replay a captured e:26 payload through _on_trade_closed.

    Uses FakeOlympTradeClient but with a real captured e:26 payload.
    """
    payload = json.loads(FIXTURE.read_text())
    notifier = RecordingNotifier()
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    broker._client = cast(OlympTradeClient, FakeOlympTradeClient())
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    future = asyncio.get_event_loop().create_future()
    broker._pending["98765"] = future

    await broker._on_trade_closed(payload)

    assert future.done()
    result = future.result()
    assert result["result"] in {"win", "loss", "tie"}
    assert isinstance(result["pnl"], Decimal)
