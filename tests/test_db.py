from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    row_to_daily_summary_row,
    row_to_signal_row,
    row_to_stage_row,
)


def _record(**fields: Any) -> Any:
    """Build a minimal asyncpg.Record-like object for mapper tests.

    asyncpg.Record supports both dict-style and attribute-style access. We
    only need dict-style for the mappers, so a plain object with __getitem__
    is sufficient.
    """

    class _R:
        def __getitem__(self, key: str) -> Any:
            return fields[key]

    return _R()


def test_row_to_signal_row_maps_all_fields() -> None:
    record = _record(
        signal_id="abc123def456",
        pair="EUR/JPY",
        broker_pair="EURJPY",
        broker_category="digital",
        direction="down",
        trigger_hhmm="10:20",
        trigger_ts_unix=1_700_000_000.0,
        expiration_seconds=300,
        received_at_unix=1_699_999_900.0,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        status="pending",
        error_reason=None,
        created_at_unix=1_699_999_900.0,
        updated_at_unix=1_699_999_900.0,
    )
    row = row_to_signal_row(record)
    assert row == SignalRow(
        signal_id="abc123def456",
        pair="EUR/JPY",
        broker_pair="EURJPY",
        broker_category="digital",
        direction="down",
        trigger_hhmm="10:20",
        trigger_ts_unix=1_700_000_000.0,
        expiration_seconds=300,
        received_at_unix=1_699_999_900.0,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        status="pending",
        error_reason=None,
        created_at_unix=1_699_999_900.0,
        updated_at_unix=1_699_999_900.0,
    )


def test_row_to_stage_row_converts_amount_float_to_decimal_via_str() -> None:
    record = _record(
        trade_id="abc1234567890123",
        signal_id="sig1",
        stage="initial",
        pair="EUR/JPY",
        direction="down",
        amount=2.0,  # float in, Decimal("2.0") out
        placed_at_unix=1_700_000_000.0,
        expires_at_unix=1_700_000_300.0,
        closed_at_unix=None,
        pnl=None,
        result="open",
        broker_trade_id="broker-123",
    )
    row = row_to_stage_row(record)
    assert row.amount == Decimal("2.0")
    assert row.pnl is None
    assert row.closed_at_unix is None


def test_row_to_stage_row_converts_pnl_float_to_decimal() -> None:
    record = _record(
        trade_id="t1",
        signal_id="s1",
        stage="initial",
        pair="EUR/JPY",
        direction="down",
        amount=2.0,
        placed_at_unix=1.0,
        expires_at_unix=2.0,
        closed_at_unix=300.0,
        pnl=1.84,
        result="win",
        broker_trade_id="b1",
    )
    row = row_to_stage_row(record)
    assert row.pnl == Decimal("1.84")
    assert row.result == "win"


def test_row_to_daily_summary_row_handles_null_limit_hit() -> None:
    record = _record(
        date=date(2026, 6, 20),
        signals_count=5,
        trades_count=10,
        wins=7,
        losses=3,
        realized_pnl=12.84,
        limit_hit=None,
    )
    row = row_to_daily_summary_row(record)
    assert row == DailySummaryRow(
        date=date(2026, 6, 20),
        signals_count=5,
        trades_count=10,
        wins=7,
        losses=3,
        realized_pnl=Decimal("12.84"),
        limit_hit=None,
    )
