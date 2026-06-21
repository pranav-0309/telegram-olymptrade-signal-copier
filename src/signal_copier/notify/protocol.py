"""The Notifier Protocol — the cross-cutting interface between M6's scheduler
and M7's Telegram DM notifier.

M6 ships a `NoOpNotifier` (logs every event at INFO). M7 implements
`TelegramDMNotifier` that satisfies the Protocol and sends the FR-7.1
messages. Tests substitute `RecordingNotifier` (in tests/_scheduler_fixtures.py).

Design contract:
  - Every method is async (M7 may need to await Telegram API calls).
  - Methods are not expected to raise. If a method body raises, M6's
    supervisor catches the exception, logs it, and continues. A failing
    DM must not abort a cascade.
  - All methods receive a frozen dataclass (Signal, etc.); notifiers must
    not mutate them.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import FailureReason, Signal

if TYPE_CHECKING:
    from signal_copier.domain.state import TerminalState
    from signal_copier.infra.db_rows import DailySummaryRow

_log = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """One method per FR-7.1 event that the scheduler emits.

    Each method's docstring cites the FR-7.1 row it implements.
    """

    async def on_signal_received(self, signal: Signal) -> None:
        """FR-7.1 row 'Signal received'. Fires immediately on parser match."""

    async def on_trade_placed(
        self,
        signal: Signal,
        stage: Stage,
        amount: Decimal,
        trade_id: str,
    ) -> None:
        """FR-7.1 rows 'Trade placed — initial/1st gale/2nd gale'."""

    async def on_win(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
    ) -> None:
        """FR-7.1 rows 'WIN — initial/1st gale/2nd gale'."""

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        """FR-7.1 rows 'LOSS — initial/1st gale/2nd gale'. `next_stage` is
        None if the loss ended the cascade (e.g., gale2 loss → done_loss)."""

    async def on_signal_expired(
        self,
        signal: Signal,
        stage: Stage,
        trigger_hhmm: str,
    ) -> None:
        """FR-7.1 rows 'Signal expired — initial/1st gale/2nd gale'."""

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: TerminalState,
        cumulative_pnl: Decimal,
    ) -> None:
        """FR-7.1 row 'Cascade end (terminal)'."""

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: DailySummaryRow,
    ) -> None:
        """FR-7.1 rows 'Daily loss/trade limit hit'. `limit_type` is
        'loss', 'count', or 'drawdown'. Fires once per rejected signal."""

    async def on_bot_started(
        self,
        *,
        mode: str,
        watching: str,
        timezone: str,
    ) -> None:
        """FR-7.1 row 'Bot startup'. Fires once per process."""

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        """FR-7.1 row 'Bot shutdown'. Fires once per process."""

    async def on_parse_failure(
        self,
        raw_text: str,
        reason: FailureReason,
    ) -> None:
        """FR-7.1 row 'Parse failure'. Fires from the M5 Listener when a
        message doesn't match the signal regex."""

    async def on_telegram_disconnect(self) -> None:
        """FR-7.1 row 'Telegram disconnect'. Fires from the M5 TelegramClient
        wrapper on ConnectionError before reconnect."""

    async def on_olymp_disconnect(self) -> None:
        """FR-7.1 row 'OlympTrade disconnect'. Fires from M8/M10's
        reconnect supervisor. M7 ships the method only — emission wiring
        lands in M8 (broker) and M10 (reconnect supervisor)."""


class NoOpNotifier:
    """Logs every method call at INFO with a structured payload. The default
    notifier for v1 (M6's wiring uses this until M7 wires TelegramDMNotifier).

    Log lines use the same payload shape M7 will write to loguru's sinks;
    the `event` key identifies the FR-7.1 row.
    """

    async def on_signal_received(self, signal: Signal) -> None:
        _log.info(
            "notify: event=signal_received signal_id=%s pair=%s direction=%s "
            "trigger=%s expiration=%ds",
            signal.signal_id,
            signal.pair,
            signal.direction,
            signal.trigger_hhmm,
            signal.expiration_seconds,
        )

    async def on_trade_placed(
        self,
        signal: Signal,
        stage: Stage,
        amount: Decimal,
        trade_id: str,
    ) -> None:
        _log.info(
            "notify: event=trade_placed signal_id=%s stage=%s amount=%s trade_id=%s",
            signal.signal_id,
            stage,
            amount,
            trade_id,
        )

    async def on_win(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
    ) -> None:
        _log.info(
            "notify: event=win signal_id=%s stage=%s pnl=%s cumulative_pnl=%s",
            signal.signal_id,
            stage,
            pnl,
            cumulative_pnl,
        )

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        _log.info(
            "notify: event=loss signal_id=%s stage=%s pnl=%s cumulative_pnl=%s next_stage=%s",
            signal.signal_id,
            stage,
            pnl,
            cumulative_pnl,
            next_stage,
        )

    async def on_signal_expired(
        self,
        signal: Signal,
        stage: Stage,
        trigger_hhmm: str,
    ) -> None:
        _log.info(
            "notify: event=signal_expired signal_id=%s stage=%s trigger_hhmm=%s",
            signal.signal_id,
            stage,
            trigger_hhmm,
        )

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: TerminalState,
        cumulative_pnl: Decimal,
    ) -> None:
        _log.info(
            "notify: event=cascade_complete signal_id=%s final_state=%s cumulative_pnl=%s",
            signal.signal_id,
            final_state,
            cumulative_pnl,
        )

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: DailySummaryRow,
    ) -> None:
        _log.warning(
            "notify: event=signal_rejected_by_limit signal_id=%s limit_type=%s "
            "losses=%s trades=%s pnl=%s",
            signal.signal_id,
            limit_type,
            summary.losses,
            summary.trades_count,
            summary.realized_pnl,
        )

    async def on_bot_started(
        self,
        *,
        mode: str,
        watching: str,
        timezone: str,
    ) -> None:
        _log.info(
            "notify: event=bot_started mode=%s watching=%s timezone=%s",
            mode,
            watching,
            timezone,
        )

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        _log.info(
            "notify: event=bot_stopping open_cascades=%d",
            open_cascades,
        )

    async def on_parse_failure(
        self,
        raw_text: str,
        reason: FailureReason,
    ) -> None:
        _log.info(
            "notify: event=parse_failure reason=%s preview=%r",
            reason.value,
            raw_text[:80],
        )

    async def on_telegram_disconnect(self) -> None:
        _log.warning("notify: event=telegram_disconnect")

    async def on_olymp_disconnect(self) -> None:
        _log.warning("notify: event=olymp_disconnect")
