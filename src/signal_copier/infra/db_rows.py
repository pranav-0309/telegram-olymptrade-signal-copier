from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from signal_copier.domain.gale import Stage
from signal_copier.domain.state import AllStates, ErrorReason

# All 6 stage-result values the `stages.result` column can hold (per the DB
# CHECK constraint and `record_stage_placed` which inserts 'open'). M2's
# `StageResult` literal only covers the 5 terminal outcomes; this row type
# widens it to include the 'open' pre-terminal state that's legitimate in
# the database but never observed in the in-memory `SignalState.result`.
StageDbResult = Literal["open", "win", "loss", "tie", "timeout", "error"]

Direction = Literal["up", "down"]


@dataclass(frozen=True, slots=True)
class SignalRow:
    signal_id: str
    pair: str
    broker_pair: str | None
    broker_category: str | None
    direction: Direction
    trigger_hhmm: str
    trigger_ts_unix: float
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
    status: AllStates
    error_reason: ErrorReason | None
    created_at_unix: float
    updated_at_unix: float


@dataclass(frozen=True, slots=True)
class StageRow:
    trade_id: str
    signal_id: str
    stage: Stage
    pair: str
    direction: Direction
    amount: Decimal
    placed_at_unix: float
    expires_at_unix: float
    closed_at_unix: float | None
    pnl: Decimal | None
    result: StageDbResult
    broker_trade_id: str | None


@dataclass(frozen=True, slots=True)
class DailySummaryRow:
    date: date
    signals_count: int
    trades_count: int
    wins: int
    losses: int
    realized_pnl: Decimal
    limit_hit: str | None  # NULL | 'loss' | 'count' | 'drawdown'


def row_to_signal_row(record: Any) -> SignalRow:
    return SignalRow(
        signal_id=record["signal_id"],
        pair=record["pair"],
        broker_pair=record["broker_pair"],
        broker_category=record["broker_category"],
        direction=record["direction"],
        trigger_hhmm=record["trigger_hhmm"],
        trigger_ts_unix=record["trigger_ts_unix"],
        expiration_seconds=record["expiration_seconds"],
        received_at_unix=record["received_at_unix"],
        source_message_id=record["source_message_id"],
        source_chat_id=record["source_chat_id"],
        raw_text=record["raw_text"],
        status=record["status"],
        error_reason=record["error_reason"],
        created_at_unix=record["created_at_unix"],
        updated_at_unix=record["updated_at_unix"],
    )


def row_to_stage_row(record: Any) -> StageRow:
    return StageRow(
        trade_id=record["trade_id"],
        signal_id=record["signal_id"],
        stage=record["stage"],
        pair=record["pair"],
        direction=record["direction"],
        amount=Decimal(str(record["amount"])),
        placed_at_unix=record["placed_at_unix"],
        expires_at_unix=record["expires_at_unix"],
        closed_at_unix=record["closed_at_unix"],
        pnl=Decimal(str(record["pnl"])) if record["pnl"] is not None else None,
        result=record["result"],
        broker_trade_id=record["broker_trade_id"],
    )


def row_to_daily_summary_row(record: Any) -> DailySummaryRow:
    return DailySummaryRow(
        date=record["date"],
        signals_count=record["signals_count"],
        trades_count=record["trades_count"],
        wins=record["wins"],
        losses=record["losses"],
        realized_pnl=Decimal(str(record["realized_pnl"])),
        limit_hit=record["limit_hit"],
    )
