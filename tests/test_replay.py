"""Unit tests for signal_copier.replay."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from signal_copier import replay


class FakeListener:
    """Captures the synthetic Telethon Message objects replay.py constructs."""

    def __init__(self) -> None:
        self.received: list[Any] = []

    async def _process_message(self, event: Any) -> None:
        self.received.append(
            {
                "text": getattr(event, "text", None) or getattr(event, "raw_text", ""),
                "chat_id": getattr(event, "chat_id", None),
                "id": getattr(event, "id", None),
            }
        )


@pytest.mark.asyncio
async def test_replay_runner_injects_each_fixture_entry(tmp_path: Path) -> None:
    """3-entry fixture → 3 calls to listener._process_message with matching texts."""
    listener = FakeListener()

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "id": "soak_001",
                    "inject_at_offset_seconds": 0,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "EUR/JPY;10:20;PUT🟥\n"
                        "🕛TIME UNTIL 10:25\n"
                        "1st GALE -> TIME UNTIL 10:25\n"
                        "2nd GALE - TIME UNTIL 10:25"
                    ),
                    "expected_outcome": "win_at_initial",
                    "notes": "first",
                },
                {
                    "id": "soak_002",
                    "inject_at_offset_seconds": 1,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "GBP/USD;11:00;CALL🟩\n"
                        "🕛TIME UNTIL 11:05\n"
                        "1st GALE -> TIME UNTIL 11:05\n"
                        "2nd GALE - TIME UNTIL 11:05"
                    ),
                    "expected_outcome": "loss_initial_win_gale1",
                    "notes": "second",
                },
                {
                    "id": "soak_003",
                    "inject_at_offset_seconds": 0.5,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "USD/CAD;12:00;PUT🟥\n"
                        "🕛TIME UNTIL 12:05\n"
                        "1st GALE -> TIME UNTIL 12:05\n"
                        "2nd GALE - TIME UNTIL 12:05"
                    ),
                    "expected_outcome": "full_loss",
                    "notes": "third",
                },
            ]
        )
    )

    import os

    os.environ["TELEGRAM_TARGET_CHAT"] = "@test_channel"

    runner_task = asyncio.create_task(
        replay.replay_runner(
            fixture_path=fixture_path,
            target_chat_id=-1001234567890,
            listener_callback=listener._process_message,
        )
    )
    await asyncio.sleep(1.5)
    runner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner_task

    assert len(listener.received) == 3
    received_pairs = {(r["text"], i) for i, r in enumerate(listener.received)}
    assert any("EUR/JPY;10:20;PUT🟥" in t for t, _ in received_pairs)
    assert any("GBP/USD;11:00;CALL🟩" in t for t, _ in received_pairs)
    assert any("USD/CAD;12:00;PUT🟥" in t for t, _ in received_pairs)


@pytest.mark.asyncio
async def test_replay_skips_malformed_entries(tmp_path: Path) -> None:
    """Entries missing required fields are logged at WARNING and skipped (not injected)."""
    listener = FakeListener()

    fixture_path = tmp_path / "bad.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "id": "good",
                    "inject_at_offset_seconds": 0,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "EUR/JPY;10:20;PUT🟥\n"
                        "🕛TIME UNTIL 10:25\n"
                        "1st GALE -> TIME UNTIL 10:25\n"
                        "2nd GALE - TIME UNTIL 10:25"
                    ),
                    "expected_outcome": "win_at_initial",
                    "notes": "",
                },
                {
                    "id": "bad-1",
                    "inject_at_offset_seconds": 0.1,
                    "expected_outcome": "win_at_initial",
                    "notes": "",
                },
                {
                    "id": "bad-2",
                    "raw_text": "...",
                    "expected_outcome": "win_at_initial",
                    "notes": "",
                },
            ]
        )
    )

    runner_task = asyncio.create_task(
        replay.replay_runner(
            fixture_path=fixture_path,
            target_chat_id=-100,
            listener_callback=listener._process_message,
        )
    )
    await asyncio.sleep(0.5)
    runner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner_task

    assert len(listener.received) == 1
    assert "EUR/JPY;10:20;PUT🟥" in listener.received[0]["text"]


@pytest.mark.asyncio
async def test_replay_skips_past_dated_entries(tmp_path: Path) -> None:
    """Entries whose inject_at_offset_seconds is negative are skipped with WARNING."""
    listener = FakeListener()

    fixture_path = tmp_path / "pastdated.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "id": "future",
                    "inject_at_offset_seconds": 0.1,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "EUR/JPY;10:20;PUT🟥\n"
                        "🕛TIME UNTIL 10:25\n"
                        "1st GALE -> TIME UNTIL 10:25\n"
                        "2nd GALE - TIME UNTIL 10:25"
                    ),
                    "expected_outcome": "win_at_initial",
                    "notes": "future",
                },
                {
                    "id": "past",
                    "inject_at_offset_seconds": -10.0,
                    "raw_text": (
                        "💰5-minute expiration\n"
                        "GBP/USD;11:00;CALL🟩\n"
                        "🕛TIME UNTIL 11:05\n"
                        "1st GALE -> TIME UNTIL 11:05\n"
                        "2nd GALE - TIME UNTIL 11:05"
                    ),
                    "expected_outcome": "win_at_initial",
                    "notes": "past",
                },
            ]
        )
    )

    runner_task = asyncio.create_task(
        replay.replay_runner(
            fixture_path=fixture_path,
            target_chat_id=-100,
            listener_callback=listener._process_message,
        )
    )
    await asyncio.sleep(0.5)
    runner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner_task

    assert len(listener.received) == 1
    assert "EUR/JPY" in listener.received[0]["text"]
