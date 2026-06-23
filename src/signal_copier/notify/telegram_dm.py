"""TelegramDMNotifier — implements the Notifier Protocol by sending self-DMs.

Each FR-7.1 event has a dedicated async method that builds the message
string and calls ``_send(text)``. ``_send`` performs the Telegram send via
the same Telethon client as the listener (FR-7.4) and mirrors the text to
loguru at INFO. Failures are logged at WARNING and swallowed (D-5: notifier
exceptions must not abort the cascade).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger as _loguru_logger

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.infra.clock import format_local_hhmm, now_unix

if TYPE_CHECKING:
    from signal_copier.domain.state import TerminalState
    from signal_copier.infra.db_rows import DailySummaryRow
    from signal_copier.telegram.client import TelegramClient


class TelegramDMNotifier:
    """Notifier that sends FR-7.1 messages to the user's 'Saved Messages'."""

    def __init__(
        self,
        *,
        tg_client: TelegramClient,
        config: Config,
    ) -> None:
        self._tg = tg_client
        self._config = config

    async def _send(self, text: str) -> None:
        """Send one DM. Log-and-swallow on any failure (D-5)."""
        try:
            await self._tg.send_to_self(text)
        except Exception as exc:
            _loguru_logger.bind(dm_event=True).warning(
                "DM send failed: text_preview={!r} exc={}", text[:80], exc
            )
            return
        _loguru_logger.bind(dm_event=True).info(text)

    def _stage_label(self, stage: Stage) -> str:
        return {"initial": "INITIAL", "gale1": "1st GALE", "gale2": "2nd GALE"}[stage]

    def _stage_gale_unix(self, signal: Signal, stage: Stage) -> float:
        """Return trigger_unix for the stage (initial=0, gale1=1, gale2=2)."""
        index = {"initial": 0, "gale1": 1, "gale2": 2}[stage]
        return signal.trigger_unix_initial + index * signal.expiration_seconds

    def _fmt_pnl(self, decimal: Decimal) -> str:
        if decimal >= 0:
            return f"+${decimal:.2f}"
        return f"${decimal:.2f}"

    def _fmt_signed(self, decimal: Decimal) -> str:
        """Format a Decimal with explicit sign (e.g. '-14.00').

        The caller typically prepends '$'.
        """
        return f"{decimal:+.2f}"

    def _duration_human(self, start_unix: float, end_unix: float) -> str:
        delta = max(0, int(end_unix - start_unix))
        return f"{delta // 60}m{delta % 60:02d}s"

    # --- Methods below are filled in by Tasks 9-13. ---

    async def on_signal_received(self, signal: Signal) -> None:
        dir_str = "CALL" if signal.direction == "up" else "PUT"
        minutes = signal.expiration_seconds // 60
        text = (
            f"🟢 Signal received\n"
            f"Pair: {signal.pair}\n"
            f"Direction: {dir_str}\n"
            f"Trigger: {signal.trigger_hhmm} (UTC-3)\n"
            f"Expiration: {minutes} min"
        )
        await self._send(text)

    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str
    ) -> None:
        label = self._stage_label(stage)
        expires_unix = self._stage_gale_unix(signal, stage) + signal.expiration_seconds
        expires_hhmm = format_local_hhmm(expires_unix, self._config.tz())
        if stage == "initial":
            # fmt: off
            dir_str = "CALL" if signal.direction == "up" else "PUT"
            text = (
                f"⏱️ Trade placed ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Direction: {dir_str}\n"
                f"Amount: ${amount:.2f}\n"
                f"Expires: {expires_hhmm} (UTC-3)\n"
                f"Trade ID: {trade_id}"
            )
            # fmt: on
        else:
            triggered_by = (
                "Triggered by: loss on initial"
                if stage == "gale1"
                else "Triggered by: loss on 1st gale"
            )
            # fmt: off
            text = (
                f"⏱️ Trade placed ({label})\n"
                f"Amount: ${amount:.2f}\n"
                f"Expires: {expires_hhmm} (UTC-3)\n"
                f"{triggered_by}\n"
                f"Trade ID: {trade_id}"
            )
            # fmt: on
        await self._send(text)

    async def on_win(
        self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal
    ) -> None:
        label = self._stage_label(stage)
        if stage == "initial":
            # fmt: off
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Signal closed: done_win\n"
                f"Next: stop (cascade ends)"
            )
            # fmt: on
        elif stage == "gale1":
            # fmt: off
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: stopped after gale1 — total recovered"
            )
            # fmt: on
        else:  # gale2
            # fmt: off
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: stopped after gale2 — full recovery"
            )
            # fmt: on
        await self._send(text)

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        label = self._stage_label(stage)
        if next_stage is None:
            # fmt: off
            text = (
                f"❌ LOSS ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: ended — full loss (${self._fmt_signed(cumulative_pnl)} total)"
            )
            # fmt: on
        else:
            next_label = {"gale1": "1st gale", "gale2": "2nd gale"}[next_stage]
            next_trigger_unix = self._stage_gale_unix(signal, next_stage)
            next_hhmm = format_local_hhmm(next_trigger_unix, self._config.tz())
            gale_amount = amount_for_stage(next_stage, self._config)
            # fmt: off
            text = (
                f"❌ LOSS ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Next: scheduling {next_label} at {next_hhmm} (UTC-3), "
                f"${gale_amount:.2f}"
            )
            # fmt: on
        await self._send(text)

    async def on_signal_expired(self, signal: Signal, stage: Stage, trigger_hhmm: str) -> None:
        label = self._stage_label(stage)
        if stage == "initial":
            # fmt: off
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: no trades placed; signal invalid"
            )
            # fmt: on
        elif stage == "gale1":
            # fmt: off
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Gale1 trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: no gale2 placed — cascade ended"
            )
            # fmt: on
        else:  # gale2
            # fmt: off
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Gale2 trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: cascade ended, no recovery attempted"
            )
            # fmt: on
        await self._send(text)

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: TerminalState,
        cumulative_pnl: Decimal,
    ) -> None:
        duration = self._duration_human(signal.received_at_unix, now_unix())
        # fmt: off
        text = (
            f"🏁 Cascade complete: {final_state}\n"
            f"Signal ID: {signal.signal_id}\n"
            f"Total PnL: ${self._fmt_signed(cumulative_pnl)}\n"
            f"Duration: {duration}"
        )
        # fmt: on
        await self._send(text)

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: DailySummaryRow,
    ) -> None:
        if limit_type == "loss":
            # fmt: off
            text = (
                f"⚠️ Daily loss limit reached\n"
                f"Losses today: {self._fmt_pnl(summary.realized_pnl)}\n"
                f"Limit: ${self._config.daily_loss_limit:.2f}\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
            # fmt: on
        elif limit_type == "count":
            # fmt: off
            text = (
                f"⚠️ Daily trade limit reached\n"
                f"Trades today: {summary.trades_count}\n"
                f"Limit: {self._config.daily_trade_limit}\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
            # fmt: on
        else:  # drawdown
            # fmt: off
            text = (
                f"⚠️ Daily drawdown limit reached\n"
                f"Drawdown today: {self._fmt_pnl(summary.realized_pnl)}\n"
                f"Limit: {self._config.daily_drawdown_pct}%\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
            # fmt: on
        await self._send(text)

    async def on_bot_started(self, *, mode: str, watching: str, timezone: str) -> None:
        # fmt: off
        text = (
            f"🟢 Bot started\n"
            f"Mode: {mode}\n"
            f"Watching: {watching}\n"
            f"Timezone: {timezone}"
        )
        # fmt: on
        await self._send(text)

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        text = f"🔴 Bot stopping\nOpen cascades: {open_cascades}"
        await self._send(text)

    async def on_parse_failure(self, raw_text: str, reason: FailureReason) -> None:
        # fmt: off
        text = (
            f"⚠️ Skipped message (not a valid signal)\n"
            f"Reason: {reason.value}\n"
            f"Preview: {raw_text[:80]}"
        )
        # fmt: on
        await self._send(text)

    async def on_telegram_disconnect(self) -> None:
        await self._send("🔌 Telegram disconnected. Reconnecting…")

    async def on_olymp_disconnect(self) -> None:
        await self._send("🔌 OlympTrade disconnected. Reconnecting…")

    async def on_olymp_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        text = (
            f"🔁 OlympTrade reconnecting (attempt {attempt}/{max_attempts})\n"
            f"Downtime: {downtime_seconds:.1f}s\n"
            f"Next retry in {next_delay_seconds:.1f}s"
        )
        await self._send(text)

    async def on_olymp_reconnected(
        self,
        *,
        attempts_used: int,
        total_downtime_seconds: float,
    ) -> None:
        text = (
            f"✅ OlympTrade reconnected\n"
            f"Attempts: {attempts_used}\n"
            f"Total downtime: {total_downtime_seconds:.1f}s\n"
            f"Action: resumed normal operation. "
            f"In-flight cascades (if any) were ended with broker_unavailable."
        )
        await self._send(text)

    async def on_olymp_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        text = (
            f"❌ OlympTrade reconnect failed after {attempts} attempts\n"
            f"Total downtime: {total_downtime_seconds:.1f}s\n"
            f"Action: process will exit; Railway supervisor will restart."
        )
        await self._send(text)
