"""Boot-time recovery of in-progress cascades (M9).

On startup, queries `signals` for rows in `placed_*` states. For each:
  1. If the stage's expiration+grace window has passed → scheduler.record_timeout()
     (the M2 state machine then applies FR-5.3 / FR-5.5-5.7 cascade advancement).
  2. Otherwise → scheduler.adopt() (re-arms the broker push listener; the
     existing M6 SignalSupervisor continues the cascade from where it left off).

`pending` signals (scheduled but not yet fired) are NOT recovered — Telegram
does not redeliver missed messages, so the signal is lost. The listener's
`MessageEdited` subscription (FR-1.5) catches re-posts. See spec §4.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signal_copier.broker.base import Broker
    from signal_copier.domain.gale import Stage
    from signal_copier.infra.db_rows import SignalRow
    from signal_copier.infra.state_store import StateStore
    from signal_copier.scheduler.trigger import Scheduler


# PRD FR-5.3: hard timeout for wait_result is expiration_seconds + 30s grace.
_STAGE_WINDOW_SECONDS: int = 330


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Outcome of one boot-time recovery run.

    `rehydrated`: signals adopted (cascade resumed via scheduler.adopt()).
    `timed_out`: signals whose stage window expired; recorded as timeout
        via scheduler.record_timeout() (state machine then advances or ends).
    `abandoned`: signals skipped (terminal status, idempotent re-run).
    """

    rehydrated: int
    timed_out: int
    abandoned: int


async def recover_active_signals(
    state_store: StateStore,
    broker: Broker,
    scheduler: Scheduler,
    *,
    now_unix: float | None = None,
) -> RecoveryReport:
    """One-shot boot-time recovery. No-op when no active signals exist."""
    active: list[SignalRow] = await state_store.get_active_signals()
    if not active:
        return RecoveryReport(rehydrated=0, timed_out=0, abandoned=0)

    rehydrated = 0
    timed_out = 0
    abandoned = 0
    now = now_unix if now_unix is not None else _now_unix()

    for signal_row in active:
        # Recovery does not reimplement the within-window check from M2.
        # We use the same trigger_ts_unix + expiration + grace math.
        # If the signal's status is already terminal (idempotent re-run),
        # skip it. get_active_signals() already filters by status, but
        # a status flip between query and recovery is possible — double-check.
        if signal_row.status in {"done_win", "done_loss", "done_tie", "error"}:
            abandoned += 1
            continue

        # We don't have stage_fire_ts from the SignalRow directly — it's
        # the latest stage's placed_at_unix. For v1 we approximate using
        # trigger_ts_unix + (stage_offset * expiration_seconds), where
        # stage_offset = 0 for initial, 1 for gale1, 2 for gale2.
        # This is sufficient because the placed stage's actual placed_at_unix
        # is recorded in the stages table (M4 record_stage_placed).
        stage_offset = _stage_offset_for_status(signal_row.status)
        stage_fire_ts = signal_row.trigger_ts_unix + (stage_offset * signal_row.expiration_seconds)

        window_end = stage_fire_ts + _STAGE_WINDOW_SECONDS
        if now > window_end:
            stage = _stage_name_for_status(signal_row.status)
            await scheduler.record_timeout(
                signal_row.signal_id,
                stage,
            )
            timed_out += 1
        else:
            await scheduler.adopt(signal_row)
            rehydrated += 1

    return RecoveryReport(rehydrated=rehydrated, timed_out=timed_out, abandoned=abandoned)


def _stage_offset_for_status(status: str) -> int:
    """Return 0 for initial, 1 for gale1, 2 for gale2."""
    if status == "placed_initial":
        return 0
    if status == "placed_gale1":
        return 1
    if status == "placed_gale2":
        return 2
    raise ValueError(f"unexpected status: {status!r}")  # pragma: no cover


def _stage_name_for_status(status: str) -> Stage:
    """Map status to stage name for record_timeout."""
    if status == "placed_initial":
        return "initial"
    if status == "placed_gale1":
        return "gale1"
    if status == "placed_gale2":
        return "gale2"
    raise ValueError(f"unexpected status: {status!r}")  # pragma: no cover


def _now_unix() -> float:
    """Local import to keep the module testable without a clock fixture."""
    from signal_copier.infra.clock import now_unix as _real

    return _real()
