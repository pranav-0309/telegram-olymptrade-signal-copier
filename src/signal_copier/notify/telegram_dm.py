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
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.infra.clock import format_local_hhmm

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
        raise NotImplementedError

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        raise NotImplementedError

    async def on_signal_expired(self, signal: Signal, stage: Stage, trigger_hhmm: str) -> None:
        raise NotImplementedError

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: TerminalState,
        cumulative_pnl: Decimal,
    ) -> None:
        raise NotImplementedError

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: DailySummaryRow,
    ) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    async def on_telegram_disconnect(self) -> None:
        await self._send("🔌 Telegram disconnected. Reconnecting…")

    async def on_olymp_disconnect(self) -> None:
        await self._send("🔌 OlympTrade disconnected. Process will exit; supervisor will restart.")
