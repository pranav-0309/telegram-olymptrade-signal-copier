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


from signal_copier.infra.db import _redact_dsn  # noqa: E402


def test_redact_dsn_strips_password_url_form() -> None:
    assert _redact_dsn("postgresql://user:secret@host:5432/db") == (
        "postgresql://user:***@host:5432/db"
    )


def test_redact_dsn_preserves_query_params() -> None:
    redacted = _redact_dsn(
        "postgresql://user:secret@host:5432/db?sslmode=require&application_name=copier"
    )
    assert redacted == (
        "postgresql://user:***@host:5432/db?sslmode=require&application_name=copier"
    )


def test_redact_dsn_handles_keyword_form() -> None:
    redacted = _redact_dsn("host=h user=u password=secret dbname=d")
    assert redacted == "host=h user=u password=*** dbname=d"


def test_redact_dsn_no_password_unchanged() -> None:
    assert _redact_dsn("postgresql://host:5432/db") == "postgresql://host:5432/db"


import pytest  # noqa: E402

from signal_copier.infra.db import Database, DatabaseConnectionError  # noqa: E402


async def test_migrations_create_expected_tables(db) -> None:
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
    table_names = {r["table_name"] for r in rows}
    assert table_names == {"signals", "stages", "daily_summary"}


async def test_migrations_create_expected_indexes(db) -> None:
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname"
        )
    index_names = {r["indexname"] for r in rows}
    assert index_names >= {
        "idx_signals_status",
        "idx_signals_trigger_ts",
        "idx_stages_signal_id",
        "idx_stages_placed_at",
        "idx_stages_result",
    }


async def test_migrations_are_idempotent(pg_dsn) -> None:
    # Second connect on the same DSN must succeed (CREATE TABLE IF NOT EXISTS).
    db1 = await Database.connect(pg_dsn)
    try:
        await db1.close()
    finally:
        pass
    db2 = await Database.connect(pg_dsn)
    try:
        # If we get here without error, idempotency is verified.
        assert db2.pool is not None
    finally:
        await db2.close()


async def test_database_connection_error_on_unreachable_host() -> None:
    bad_dsn = "postgresql://nobody:nopass@127.0.0.1:1/nodb"
    with pytest.raises(DatabaseConnectionError) as excinfo:
        await Database.connect(bad_dsn)
    assert "Cannot reach PostgreSQL" in str(excinfo.value)


async def test_database_connection_error_redacts_password() -> None:
    bad_dsn = "postgresql://user:supersecret@127.0.0.1:1/nodb"
    with pytest.raises(DatabaseConnectionError) as excinfo:
        await Database.connect(bad_dsn)
    assert "supersecret" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


from datetime import date as _date  # noqa: E402

from signal_copier.domain.signal import Signal, derive_signal_id  # noqa: E402


def _make_signal(
    pair: str = "EUR/JPY",
    direction: str = "down",
    trigger_hhmm: str = "10:20",
    signal_date: _date | None = None,
) -> Signal:
    """Build a Signal dataclass for tests. trigger_unix_X computed from now."""
    import time

    now = time.time()
    # Trigger at "now + 60s" so it's in the future.
    trigger_initial = now + 60.0
    parsed = type(
        "P",
        (),
        {
            "pair": pair,
            "direction": direction,
            "trigger_hhmm": trigger_hhmm,
        },
    )()  # minimal stand-in for ParsedSignal
    sid = derive_signal_id(parsed, signal_date=signal_date or _date.today())
    return Signal(
        signal_id=sid,
        pair=pair,
        direction=direction,  # type: ignore[arg-type]
        trigger_hhmm=trigger_hhmm,
        expiration_seconds=300,
        received_at_unix=now,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=trigger_initial,
        trigger_unix_gale1=trigger_initial + 300.0,
        trigger_unix_gale2=trigger_initial + 600.0,
    )


async def test_upsert_signal_inserts_new_returns_true(db) -> None:
    signal = _make_signal()
    inserted = await db.state_store.upsert_signal(signal)
    assert inserted is True


async def test_upsert_signal_duplicate_returns_false(db) -> None:
    signal = _make_signal()
    first = await db.state_store.upsert_signal(signal)
    second = await db.state_store.upsert_signal(signal)
    assert first is True
    assert second is False


async def test_get_signal_returns_none_for_missing(db) -> None:
    assert await db.state_store.get_signal("nonexistent") is None


async def test_get_signal_round_trip(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.signal_id == signal.signal_id
    assert row.pair == "EUR/JPY"
    assert row.direction == "down"
    assert row.trigger_hhmm == "10:20"
    assert row.status == "pending"
    assert row.error_reason is None
    assert row.expiration_seconds == 300
    assert row.source_message_id == 42


async def test_update_signal_state_round_trip(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    # pending → placed_initial
    await db.state_store.update_signal_state(
        signal.signal_id,
        "placed_initial",
        updated_at_unix=1.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "placed_initial"
    assert row.error_reason is None
    # placed_initial → done_win
    await db.state_store.update_signal_state(
        signal.signal_id,
        "done_win",
        updated_at_unix=2.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "done_win"


async def test_update_signal_state_with_error_reason(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    await db.state_store.update_signal_state(
        signal.signal_id,
        "error",
        error_reason="signal_expired",
        updated_at_unix=1.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "error"
    assert row.error_reason == "signal_expired"


async def test_update_signal_state_warns_on_missing_signal_id(db, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        await db.state_store.update_signal_state(
            "nonexistent-id",
            "done_win",
            updated_at_unix=1.0,
        )
    assert any("no row" in r.message for r in caplog.records)


from decimal import Decimal as _D  # noqa: E402

from signal_copier.infra.state_store import StageAlreadyExistsError, StateStore  # noqa: E402


async def test_record_stage_placed_returns_deterministic_trade_id(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid1 = await db.state_store.record_stage_placed(
        signal.signal_id,
        "initial",
        pair=signal.pair,
        direction=signal.direction,
        amount=_D("2.00"),
        placed_at_unix=1.0,
        expires_at_unix=301.0,
    )
    # Same args → same ID (computed independently, not from DB)
    expected = StateStore._derive_trade_id(signal.signal_id, "initial", 1.0)
    assert tid1 == expected
    assert len(tid1) == 16


async def test_record_stage_placed_inserts_row_with_all_fields(db) -> None:
    from signal_copier.infra.db_rows import StageRow

    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid = await db.state_store.record_stage_placed(
        signal.signal_id,
        "initial",
        pair=signal.pair,
        direction=signal.direction,
        amount=_D("2.00"),
        placed_at_unix=1_700_000_000.0,
        expires_at_unix=1_700_000_300.0,
        broker_trade_id="broker-abc",
    )
    # Read it back via raw SQL to confirm the row exists with the right fields.
    async with db.pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM stages WHERE trade_id = $1",
            tid,
        )
    assert record is not None
    row = row_to_stage_row(record)  # type: ignore[arg-type]
    assert row == StageRow(
        trade_id=tid,
        signal_id=signal.signal_id,
        stage="initial",
        pair=signal.pair,
        direction=signal.direction,
        amount=_D("2.00"),
        placed_at_unix=1_700_000_000.0,
        expires_at_unix=1_700_000_300.0,
        closed_at_unix=None,
        pnl=None,
        result="open",
        broker_trade_id="broker-abc",
    )


async def test_record_stage_placed_raises_on_duplicate(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    kwargs = dict(
        pair=signal.pair,
        direction=signal.direction,
        amount=_D("2.00"),
        placed_at_unix=1.0,
        expires_at_unix=301.0,
    )
    await db.state_store.record_stage_placed(
        signal.signal_id,
        "initial",
        **kwargs,
    )
    with pytest.raises(StageAlreadyExistsError):
        await db.state_store.record_stage_placed(
            signal.signal_id,
            "initial",
            **kwargs,
        )
