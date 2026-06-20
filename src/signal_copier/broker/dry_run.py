from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)

# OutcomeProvider is async so M8's real broker (or future tests needing IO)
# can be a drop-in. The default and most test providers are sync internally;
# they still need `async def` to match this signature.
OutcomeProvider = Callable[[Signal, Stage], Awaitable[StageResult]]


async def _default_outcome(signal: Signal, stage: Stage) -> StageResult:
    """Default outcome provider: every trade wins.

    Matches the analyst's signal strategy in real-world conditions
    (90%+ of signals hit before gale2). M9 soak uses this default.
    """
    _ = signal, stage
    return "win"


@dataclass(slots=True)
class DryRunBroker:
    """Logs intended trades and returns a configurable outcome without ever
    touching a real broker. Default for v1 (FR-6.5: DRY_RUN=true).

    Not frozen: holds an internal _placed dict mapping trade_id to
    (signal, stage) so wait_result can call outcome_provider(signal, stage).
    The dict is bounded — wait_result pops its entry, so growth is
    O(in-flight trades), not O(all-time trades).
    """

    outcome_provider: OutcomeProvider = _default_outcome
    account_group: str = "demo"  # informational only; M2's Config guardrail
    # is the authoritative enforcement
    _placed: dict[str, tuple[Signal, Stage]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    _PREFIX: ClassVar[str] = "dryrun"  # trade_id prefix; makes DB rows identifiable

    async def connect(self) -> None:
        _log.info(
            "DryRunBroker connected (account_group=%s)",
            self.account_group,
        )

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        trade_id = f"{self._PREFIX}-{signal.signal_id}-{stage}-{uuid4().hex[:8]}"
        self._placed[trade_id] = (signal, stage)
        _log.info(
            "DRY-RUN place: pair=%s direction=%s stage=%s amount=%s signal_id=%s trade_id=%s",
            signal.pair,
            signal.direction,
            stage,
            amount,
            signal.signal_id,
            trade_id,
        )
        return trade_id

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,  # noqa: ARG002 — dry-run ignores timeout (D-7)
    ) -> StageResult:
        _log.info("DRY-RUN wait_result: trade_id=%s (instant)", trade_id)
        try:
            signal, stage = self._placed.pop(trade_id)
        except KeyError:
            # Defensive: unknown trade_id means a caller bug. M6 is the only
            # caller; this should never fire in production. Surface it as
            # 'error' so the state machine ends the cascade cleanly.
            _log.warning(
                "DRY-RUN wait_result: unknown trade_id=%s; returning 'error'",
                trade_id,
            )
            return "error"
        return await self.outcome_provider(signal, stage)

    async def close(self) -> None:
        _log.info("DryRunBroker closed")
