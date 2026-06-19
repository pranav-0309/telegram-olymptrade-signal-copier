from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal, cast

from signal_copier.config import Config

Stage = Literal["initial", "gale1", "gale2"]


# Stage → config field name. The mapping is fixed; we use a table instead of
# a chain of if/elif so adding a future "gale3" only touches the table.
_STAGE_AMOUNT_FIELD: Final[dict[str, str]] = {
    "initial": "amount_initial",
    "gale1": "amount_gale1",
    "gale2": "amount_gale2",
}


def amount_for_stage(stage: Stage, config: Config) -> Decimal:
    """Return the bet amount for a stage. Stage amounts, not increments (R-2)."""
    field = _STAGE_AMOUNT_FIELD.get(stage)
    if field is None:  # pragma: no cover — Literal type blocks this
        raise ValueError(f"unknown stage: {stage!r}")
    return cast(Decimal, getattr(config, field))


def compute_gale_triggers(
    trigger_unix_initial: float,
    expiration_seconds: int,
) -> tuple[float, float]:
    """Return (trigger_unix_gale1, trigger_unix_gale2) for a signal's initial trigger.

    Gale1 fires at initial + 1 expiration. Gale2 at initial + 2 expirations.
    R-2 + FR-5.5/5.6: stage times, not absolute offsets.
    """
    gale1 = trigger_unix_initial + float(expiration_seconds)
    gale2 = trigger_unix_initial + 2.0 * float(expiration_seconds)
    return (gale1, gale2)
