from __future__ import annotations

import logging

import asyncpg  # type: ignore[import-untyped]  # asyncpg ships no type stubs

from signal_copier.domain.signal import Signal
from signal_copier.infra.db_rows import SignalRow, row_to_signal_row

_log = logging.getLogger(__name__)


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
