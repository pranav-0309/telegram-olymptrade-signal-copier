from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal

# --- Top-level signal state machine states (PRD FR-5.1) ------------------

# Pre-terminal: the cascade is in flight.
State = Literal[
    "pending",  # signal received, not yet fired
    "placed_initial",  # initial trade placed, awaiting result
    "placed_gale1",  # gale1 trade placed, awaiting result
    "placed_gale2",  # gale2 trade placed, awaiting result
]

# Terminal: cascade complete.
# Per D-7: done_tie is reserved for v2 (unreachable in M2's transitions).
# done_win / done_loss are the only signal-level terminals in v1.
# error carries error_reason for signal_expired / broker_unavailable / unknown.
TerminalState = Literal[
    "done_win",
    "done_loss",
    "done_tie",  # reserved for v2; M2 transitions never reach this
    "error",
]
AllStates = State | TerminalState


# Per-stage result (recorded in SignalState.result, not the top-level state).
# Tie and timeout at a non-final stage are treated as LOSS for cascade purposes (FR-5.3);
# the original outcome is still recorded here for observability.
StageResult = Literal["win", "loss", "tie", "timeout", "error"]


# --- Error reason enum (subset of PRD §9.1 + FR-5.1) ---------------------

ErrorReason = Literal[
    "signal_expired",  # FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: time window passed
    "broker_unavailable",  # FR-4.4: broker dropped / token expired
    "unknown",
]


# --- Event types (M6 dispatches these based on scheduler / broker signals) ---


@dataclass(frozen=True, slots=True)
class FireEvent:
    """Try to fire the current stage's trade. Triggered by M6's asyncio.call_at."""

    now_unix: float


@dataclass(frozen=True, slots=True)
class ResultEvent:
    """The broker reports a result for the current stage's trade."""

    result: StageResult
    now_unix: float


Event = FireEvent | ResultEvent


# --- The state value (frozen, replaceable) --------------------------------


@dataclass(frozen=True, slots=True)
class SignalState:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    state: AllStates  # one of the State / TerminalState values
    stage: Stage | None  # current stage; None at terminal states
    amount: Decimal  # bet amount for the current stage
    trigger_unix: float  # trigger time for the current stage
    expires_at_unix: float  # trigger_unix + expiration_seconds
    result: StageResult | None  # last stage result (None until first result)
    cumulative_pnl: Decimal  # sum of stage PnLs (signed; losses negative)
    error_reason: ErrorReason | None  # populated only when state == "error"

    @classmethod
    def from_signal(cls, signal: Signal, config: Config) -> SignalState:
        """Construct the initial state for a newly-received signal.

        The signal is 'pending' — the scheduler hasn't fired it yet.
        Gale trigger times come from the Signal's pre-computed fields (D-5).
        """
        return cls(
            signal_id=signal.signal_id,
            pair=signal.pair,
            direction=signal.direction,
            state="pending",
            stage="initial",
            amount=amount_for_stage("initial", config),
            trigger_unix=signal.trigger_unix_initial,
            expires_at_unix=signal.trigger_unix_initial + float(signal.expiration_seconds),
            result=None,
            cumulative_pnl=Decimal("0.00"),
            error_reason=None,
        )
