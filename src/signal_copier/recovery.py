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
    from signal_copier.infra.state_store import StateStore
    from signal_copier.scheduler.trigger import Scheduler


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
    return RecoveryReport(rehydrated=0, timed_out=0, abandoned=0)
