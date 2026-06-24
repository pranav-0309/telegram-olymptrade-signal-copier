from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import asyncpg
import asyncpg.exceptions

from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    row_to_daily_summary_row,
    row_to_signal_row,
    row_to_stage_row,
)

if TYPE_CHECKING:
    from signal_copier.infra.db import Database


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


async def test_migrations_create_expected_tables(db: Database) -> None:
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
    table_names = {r["table_name"] for r in rows}
    assert table_names == {"signals", "stages", "daily_summary"}


async def test_migrations_create_expected_indexes(db: Database) -> None:
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


async def test_migrations_are_idempotent(pg_dsn: str) -> None:
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


async def test_upsert_signal_inserts_new_returns_true(db: Database) -> None:
    signal = _make_signal()
    inserted = await db.state_store.upsert_signal(signal)
    assert inserted is True


async def test_upsert_signal_duplicate_returns_false(db: Database) -> None:
    signal = _make_signal()
    first = await db.state_store.upsert_signal(signal)
    second = await db.state_store.upsert_signal(signal)
    assert first is True
    assert second is False


async def test_get_signal_returns_none_for_missing(db: Database) -> None:
    assert await db.state_store.get_signal("nonexistent") is None


async def test_get_signal_round_trip(db: Database) -> None:
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


async def test_update_signal_state_round_trip(db: Database) -> None:
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


async def test_update_signal_state_with_error_reason(db: Database) -> None:
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


async def test_update_signal_state_warns_on_missing_signal_id(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        await db.state_store.update_signal_state(
            "nonexistent-id",
            "done_win",
            updated_at_unix=1.0,
        )
    assert any("no row" in r.message for r in caplog.records)


from decimal import Decimal as _D  # noqa: E402

from signal_copier.infra.state_store import StageAlreadyExistsError, StateStore  # noqa: E402


async def test_record_stage_placed_returns_deterministic_trade_id(db: Database) -> None:
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


async def test_record_stage_placed_inserts_row_with_all_fields(db: Database) -> None:
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


async def test_record_stage_placed_raises_on_duplicate(db: Database) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    kwargs: dict[str, Any] = dict(
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


async def test_record_stage_result_updates_row(db: Database) -> None:
    from signal_copier.infra.db_rows import row_to_stage_row

    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid = await db.state_store.record_stage_placed(
        signal.signal_id,
        "initial",
        pair=signal.pair,
        direction=signal.direction,
        amount=_D("2.00"),
        placed_at_unix=1.0,
        expires_at_unix=301.0,
    )
    await db.state_store.record_stage_result(
        tid,
        "win",
        pnl=_D("1.84"),
        closed_at_unix=400.0,
    )
    async with db.pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM stages WHERE trade_id = $1",
            tid,
        )
    assert record is not None
    row = row_to_stage_row(record)  # type: ignore[arg-type]
    assert row.result == "win"
    assert row.pnl == _D("1.84")
    assert row.closed_at_unix == 400.0


async def test_record_stage_result_warns_on_missing_trade_id(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        await db.state_store.record_stage_result(
            "nonexistent-trade-id",
            "win",
            pnl=_D("1.84"),
            closed_at_unix=400.0,
        )
    assert any("no row" in r.message for r in caplog.records)


async def test_get_active_signals_excludes_terminal_states(db: Database) -> None:
    for status in ("placed_initial", "done_win", "error"):
        sig = _make_signal(trigger_hhmm=status)
        await db.state_store.upsert_signal(sig)
        await db.state_store.update_signal_state(
            sig.signal_id,
            status,  # type: ignore[arg-type]
            error_reason="signal_expired" if status == "error" else None,
            updated_at_unix=1.0,
        )
    active = await db.state_store.get_active_signals()
    assert len(active) == 1
    assert active[0].status == "placed_initial"


async def test_update_daily_summary_inserts_new_row(db: Database) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(
        today,
        signals_count_delta=1,
        trades_count_delta=2,
        wins_delta=1,
        losses_delta=1,
        realized_pnl_delta=_D("1.84"),
    )
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.signals_count == 1
    assert row.trades_count == 2
    assert row.wins == 1
    assert row.losses == 1
    assert row.realized_pnl == _D("1.84")
    assert row.limit_hit is None


async def test_update_daily_summary_adds_deltas(db: Database) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(today, wins_delta=1)
    await db.state_store.update_daily_summary(today, wins_delta=1)
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.wins == 2


async def test_update_daily_summary_preserves_limit_hit(db: Database) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(today, limit_hit="loss")
    await db.state_store.update_daily_summary(today, wins_delta=1)
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.limit_hit == "loss"
    assert row.wins == 1


async def test_update_daily_summary_concurrent(db: Database) -> None:
    import asyncio

    today = _date.today()
    await asyncio.gather(
        *[db.state_store.update_daily_summary(today, signals_count_delta=1) for _ in range(10)]
    )
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.signals_count == 10


async def test_get_daily_summary_returns_none_for_missing(db: Database) -> None:
    assert await db.state_store.get_daily_summary(_date(2020, 1, 1)) is None


async def test_command_timeout_aborts_long_query(db: Database) -> None:
    # Use a fresh connection with a tight statement_timeout. SET LOCAL only
    # takes effect within a transaction, so we open one explicitly.
    async with db.pool.acquire() as conn:
        with pytest.raises((asyncio.TimeoutError, asyncpg.exceptions.QueryCanceledError)):
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = 100")
                await conn.fetch("SELECT pg_sleep(2)")


async def test_pool_reconnect_after_backend_terminated(db: Database) -> None:
    # Acquire a connection, ask PG to kill it, release, then verify the
    # next acquire returns a working connection within 5 seconds.
    conn = await db.pool.acquire()
    try:
        pid = await conn.fetchval("SELECT pg_backend_pid()")
        # Kill our own backend from a separate connection.
        async with db.pool.acquire() as killer:
            await killer.execute(
                "SELECT pg_terminate_backend($1)",
                pid,
            )
    finally:
        # The connection is now dead; pool's release calls reset() which
        # raises because the protocol is in a weird state. Pool will discard
        # the broken connection internally; we just swallow the error.
        with contextlib.suppress(Exception):
            await db.pool.release(conn)
    # The next acquire must give us a fresh, working connection.
    async with asyncio.timeout(5.0):
        async with db.pool.acquire() as fresh_conn:
            result = await fresh_conn.fetchval("SELECT 1")
            assert result == 1
