from signal_copier.domain.gale import (
    Stage,
    amount_for_stage,
    compute_gale_triggers,
)
from signal_copier.domain.signal import (
    FailureReason,
    ParsedSignal,
    ParseFailure,
    ParseResult,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.domain.state import (
    AllStates,
    ErrorReason,
    Event,
    FireEvent,
    ResultEvent,
    SignalState,
    StageResult,
    State,
    TerminalState,
    TransitionResult,
    transition,
)

__all__ = [
    # Gale
    "Stage",
    "amount_for_stage",
    "compute_gale_triggers",
    # Signal (M1)
    "FailureReason",
    "ParseFailure",
    "ParsedSignal",
    "ParseResult",
    "Signal",
    "derive_signal_id",
    "parse_signal",
    # State machine (M2)
    "AllStates",
    "ErrorReason",
    "Event",
    "FireEvent",
    "ResultEvent",
    "SignalState",
    "StageResult",
    "State",
    "TerminalState",
    "TransitionResult",
    "transition",
]
