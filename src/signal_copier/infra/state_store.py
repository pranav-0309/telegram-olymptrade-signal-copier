from __future__ import annotations

import hashlib
import logging
from datetime import date
from decimal import Decimal
from typing import Literal

import asyncpg  # asyncpg ships no type stubs (pyproject.toml override covers this)

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason, StageResult
from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    row_to_daily_summary_row,
    row_to_signal_row,
)

_log = logging.getLogger(__name__)


class StageAlreadyExistsError(Exception):
    """Raised by StateStore.record_stage_placed() on a trade_id collision.

    This is a programming bug, not a normal runtime event: the deterministic
    trade_id derivation means a duplicate call with the same
    (signal_id, stage, placed_at_unix) is either a caller bug or a
    misbehaving restart-recovery path.
    """


class StateStore:
    """CRUD API over the signal_copier PostgreSQL schema.

    All write methods acquire a connection from the pool, run inside a
    transaction, and return. All read methods acquire a connection and
    return. asyncpg exceptions bubble up untouched.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_signal(self, signal: Signal) -> bool:
        """INSERT the signal; on signal_id conflict, do nothing.

        Returns True if the row was newly inserted, False if it already
        existed.
        """
        sql = """
            INSERT INTO signals (
                signal_id, pair, broker_pair, broker_category, direction,
                trigger_hhmm, trigger_ts_unix, expiration_seconds,
                received_at_unix, source_message_id, source_chat_id, raw_text,
                status, error_reason, created_at_unix, updated_at_unix
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                'pending', NULL, $9, $9
            )
            ON CONFLICT (signal_id) DO NOTHING
            RETURNING signal_id
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchval(
                sql,
                signal.signal_id,
                signal.pair,
                None,
                None,
                signal.direction,
                signal.trigger_hhmm,
                signal.trigger_unix_initial,
                signal.expiration_seconds,
                signal.received_at_unix,
                signal.source_message_id,
                signal.source_chat_id,
                signal.raw_text,
            )
        return row is not None

    async def get_signal(self, signal_id: str) -> SignalRow | None:
        """Fetch one signal by id. None if not found."""
        sql = "SELECT * FROM signals WHERE signal_id = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, signal_id)
        if record is None:
            return None
        return row_to_signal_row(record)

    async def update_signal_state(
        self,
        signal_id: str,
        new_state: AllStates,
        *,
        error_reason: ErrorReason | None = None,
        updated_at_unix: float,
    ) -> None:
        """Persist a state-machine transition for an existing signal.

        Idempotent: a re-write with the same new_state is a no-op. If
        signal_id doesn't exist, logs a warning and returns.
        """
        sql = """
            UPDATE signals
            SET status = $1, error_reason = $2, updated_at_unix = $3
            WHERE signal_id = $4
        """
        async with self._pool.acquire() as conn, conn.transaction():
            result = await conn.execute(
                sql,
                new_state,
                error_reason,
                updated_at_unix,
                signal_id,
            )
        if result.endswith(" 0"):
            _log.warning(
                "update_signal_state: no row for signal_id=%s (idempotent no-op)",
                signal_id,
            )

    async def record_stage_placed(
        self,
        signal_id: str,
        stage: Stage,
        *,
        pair: str,
        direction: Literal["up", "down"],
        amount: Decimal,
        placed_at_unix: float,
        expires_at_unix: float,
        broker_trade_id: str | None = None,
    ) -> str:
        """INSERT a row in `stages` marking a trade as placed.

        Returns the deterministic trade_id:
            sha1(f"{signal_id}|{stage}|{placed_at_unix:.6f}").hexdigest()[:16]
        Raises StageAlreadyExistsError if the same (signal_id, stage, placed_at_unix)
        is recorded twice.
        """
        trade_id = self._derive_trade_id(signal_id, stage, placed_at_unix)
        sql = """
            INSERT INTO stages (
                trade_id, signal_id, stage, pair, direction, amount,
                placed_at_unix, expires_at_unix, result, broker_trade_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open', $9)
            ON CONFLICT (trade_id) DO NOTHING
            RETURNING trade_id
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchval(
                sql,
                trade_id,
                signal_id,
                stage,
                pair,
                direction,
                amount,
                placed_at_unix,
                expires_at_unix,
                broker_trade_id,
            )
        if row is None:
            raise StageAlreadyExistsError(
                f"stages.trade_id={trade_id} already exists "
                f"(signal_id={signal_id} stage={stage} "
                f"placed_at_unix={placed_at_unix:.6f})"
            )
        return trade_id

    @staticmethod
    def _derive_trade_id(
        signal_id: str,
        stage: Stage,
        placed_at_unix: float,
    ) -> str:
        """Deterministic 16-char trade_id."""
        payload = f"{signal_id}|{stage}|{placed_at_unix:.6f}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    async def record_stage_result(
        self,
        trade_id: str,
        result: StageResult,
        *,
        pnl: Decimal,
        closed_at_unix: float,
    ) -> None:
        """Update an existing `stages` row with the broker-reported result.

        Idempotent: re-writing the same result is a no-op. If trade_id
        doesn't exist, logs a warning.
        """
        sql = """
            UPDATE stages
            SET result = $1, pnl = $2, closed_at_unix = $3
            WHERE trade_id = $4
        """
        async with self._pool.acquire() as conn, conn.transaction():
            tag = await conn.execute(sql, result, pnl, closed_at_unix, trade_id)
        if tag.endswith(" 0"):
            _log.warning(
                "record_stage_result: no row for trade_id=%s (late push event?)",
                trade_id,
            )

    async def get_active_signals(self) -> list[SignalRow]:
        """Fetch all signals currently in placed_* states (restart recovery input)."""
        sql = """
            SELECT * FROM signals
            WHERE status IN ('placed_initial', 'placed_gale1', 'placed_gale2')
            ORDER BY trigger_ts_unix
        """
        async with self._pool.acquire() as conn:
            records = await conn.fetch(sql)
        return [row_to_signal_row(r) for r in records]

    async def update_daily_summary(
        self,
        on_date: date,
        *,
        signals_count_delta: int = 0,
        trades_count_delta: int = 0,
        wins_delta: int = 0,
        losses_delta: int = 0,
        realized_pnl_delta: Decimal = Decimal("0"),
        limit_hit: str | None = None,
    ) -> None:
        """UPSERT a row in `daily_summary` with additive deltas.

        Passing limit_hit=None leaves the existing value unchanged; passing a
        string sets/replaces it.
        """
        sql = """
            INSERT INTO daily_summary (
                date, signals_count, trades_count, wins, losses,
                realized_pnl, limit_hit
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (date) DO UPDATE SET
                signals_count = daily_summary.signals_count + EXCLUDED.signals_count,
                trades_count  = daily_summary.trades_count  + EXCLUDED.trades_count,
                wins          = daily_summary.wins          + EXCLUDED.wins,
                losses        = daily_summary.losses        + EXCLUDED.losses,
                realized_pnl  = daily_summary.realized_pnl  + EXCLUDED.realized_pnl,
                limit_hit     = COALESCE(EXCLUDED.limit_hit, daily_summary.limit_hit)
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                sql,
                on_date,
                signals_count_delta,
                trades_count_delta,
                wins_delta,
                losses_delta,
                realized_pnl_delta,
                limit_hit,
            )

    async def get_daily_summary(self, on_date: date) -> DailySummaryRow | None:
        """Fetch one daily summary by date. None if no row yet (clean day)."""
        sql = "SELECT * FROM daily_summary WHERE date = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, on_date)
        if record is None:
            return None
        return row_to_daily_summary_row(record)
