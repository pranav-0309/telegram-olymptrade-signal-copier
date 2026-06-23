"""Opt-in fixture-driven signal injector (M9 soak).

Activated only when `SOAK_REPLAY=<path>` is set in the environment. Reads
a JSON fixture of recorded signal messages and feeds synthetic Telethon
`Message` objects to the listener's `_process_message` handler at
configured offsets from boot time.

Bypasses the Telethon event dispatch — the listener's handler is what
Telethon would call per event, so this still exercises the full parse →
Signal → queue path. We do NOT want to soak Telethon's reconnect behavior
(M5 unit-tests cover that; M9 soak just probes liveness separately).

NEVER imported in production unless `SOAK_REPLAY` is set. Gate is in
`__main__.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReplayEntry:
    """One recorded signal-message entry from the fixture file."""

    id: str
    inject_at_offset_seconds: float
    raw_text: str
    expected_outcome: str
    notes: str


ListenerCallback = Callable[[Any], Awaitable[None]]

_REQUIRED_KEYS = ("id", "inject_at_offset_seconds", "raw_text", "expected_outcome")


def load_fixture(path: Path) -> list[ReplayEntry]:
    """Read the JSON fixture and parse into ReplayEntry dataclasses.

    Entries missing any of the required keys are logged at WARNING and
    skipped (so a single bad row doesn't break the whole soak).
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[ReplayEntry] = []
    for item in raw:
        if not all(k in item for k in _REQUIRED_KEYS):
            _log.warning(
                "replay: skipping malformed entry (missing required key): %s",
                {k: item.get(k) for k in _REQUIRED_KEYS},
            )
            continue
        try:
            entries.append(
                ReplayEntry(
                    id=str(item["id"]),
                    inject_at_offset_seconds=float(item["inject_at_offset_seconds"]),
                    raw_text=str(item["raw_text"]),
                    expected_outcome=str(item["expected_outcome"]),
                    notes=str(item.get("notes", "")),
                )
            )
        except (TypeError, ValueError) as exc:
            _log.warning(
                "replay: skipping malformed entry (parse error %s): %s",
                exc,
                item,
            )
    return entries


def _build_synthetic_message(
    *,
    raw_text: str,
    chat_id: int,
    message_id: int,
) -> Any:
    """Construct a duck-typed Telethon Message with the given fields."""
    msg = type("M", (), {})()
    inner = type("Inner", (), {})()
    inner.out = False
    inner.id = message_id
    msg.message = inner
    msg.chat_id = chat_id
    msg.text = raw_text
    msg.raw_text = raw_text
    return msg


async def replay_runner(
    *,
    fixture_path: Path,
    target_chat_id: int,
    listener_callback: ListenerCallback,
) -> None:
    """Schedule each fixture entry's injection at its configured offset.

    Runs forever; the caller (the soak harness or the test) cancels it.
    Past-dated entries (offset < 0) are skipped with a WARNING log.
    """
    boot_unix = time.time()
    loop = asyncio.get_running_loop()
    entries = load_fixture(fixture_path)
    _log.info(
        "replay: loaded %d valid entries from %s; scheduling injections",
        len(entries),
        fixture_path,
    )

    async def _inject(entry: ReplayEntry) -> None:
        msg = _build_synthetic_message(
            raw_text=entry.raw_text,
            chat_id=target_chat_id,
            message_id=int(time.time() * 1000) % 1_000_000_000,
        )
        _log.info(
            "replay: injecting entry=%s offset=%.1fs raw_text=%r",
            entry.id,
            entry.inject_at_offset_seconds,
            entry.raw_text[:60],
        )
        event = type("E", (), {})()
        event.message = msg.message
        event.chat_id = msg.chat_id
        event.text = msg.text
        await listener_callback(event)

    for entry in entries:
        if entry.inject_at_offset_seconds < 0:
            _log.warning(
                "replay: skipping past-dated entry: id=%s offset=%.1f",
                entry.id,
                entry.inject_at_offset_seconds,
            )
            continue
        fire_at = boot_unix + entry.inject_at_offset_seconds

        def _schedule(e: ReplayEntry) -> None:
            asyncio.create_task(_inject(e))

        loop.call_at(
            max(fire_at - time.time(), 0) + loop.time(),
            lambda e=entry: _schedule(e),  # type: ignore[misc]
        )

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        _log.info("replay: cancelled")
        raise
