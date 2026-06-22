"""Unit tests for signal_copier.recovery."""

from __future__ import annotations

import pytest

from signal_copier.recovery import (
    RecoveryReport,
    recover_active_signals,
)
from tests._scheduler_fixtures import FakeStateStore


class RecordingScheduler:
    """Captures adopt() and record_timeout() calls from recovery."""

    def __init__(self) -> None:
        self.adopted: list[tuple[str, str]] = []  # (signal_id, status)
        self.timed_out: list[tuple[str, str]] = []  # (signal_id, stage)

    async def adopt(self, signal_row: object) -> None:
        # signal_row is signal_copier.infra.db_rows.SignalRow
        # recovery passes the row through; we just record its id + status.
        self.adopted.append((signal_row.signal_id, signal_row.status))

    async def record_timeout(self, signal_id: str, stage: str) -> None:
        self.timed_out.append((signal_id, stage))


@pytest.mark.asyncio
async def test_recover_active_signals_returns_empty_report_when_no_active_signals() -> None:
    """No placed_* signals → report counts are all zero, no scheduler calls."""
    store = FakeStateStore()
    store.signals = {}  # nothing in placed_* states
    scheduler = RecordingScheduler()

    report = await recover_active_signals(
        state_store=store,  # type: ignore[arg-type]
        broker=object(),  # unused when no signals to recover
        scheduler=scheduler,  # type: ignore[arg-type]
        now_unix=1_700_000_000.0,
    )

    assert isinstance(report, RecoveryReport)
    assert report.rehydrated == 0
    assert report.timed_out == 0
    assert report.abandoned == 0
    assert scheduler.adopted == []
    assert scheduler.timed_out == []
