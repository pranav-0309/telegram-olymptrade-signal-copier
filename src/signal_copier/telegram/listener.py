from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from signal_copier.config import Config
from signal_copier.domain.signal import (
    ParseFailure,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.infra.clock import (
    hhmm_to_unix,
    is_within_window,
    now_unix,
    signal_date_in_tz,
)
from signal_copier.infra.state_store import StateStore
from signal_copier.notify.protocol import Notifier

_log = logging.getLogger(__name__)


def _allowed_expirations(config: Config) -> frozenset[int]:
    return frozenset({config.expiration_seconds})


class Listener:
    """Wires Telethon NewMessage/MessageEdited events into the M1 parser
    and M4 StateStore. Filter-aware: only `chat_id == target` and
    non-outgoing messages are processed. A successful parse goes through
    the M1 parser, the M5 time-window check, M4's StateStore.upsert_signal,
    and finally lands on the asyncio.Queue for M6 (or M5's dump_consumer)
    to drain.
    """

    def __init__(
        self,
        *,
        target_chat_id: int,
        state_store: StateStore,
        queue: asyncio.Queue[Signal],
        config: Config,
        parse_failures_logger: logging.Logger,
        notifier: Notifier,
    ) -> None:
        self._target_chat_id = target_chat_id
        self._state_store = state_store
        self._queue = queue
        self._config = config
        self._parse_failures_logger = parse_failures_logger
        self._notifier = notifier
        self._allowed_expirations = _allowed_expirations(config)

    async def on_new_message(self, event: Any) -> None:
        await self._process_message(event)

    async def on_message_edited(self, event: Any) -> None:
        await self._process_message(event)

    async def _process_message(self, event: Any) -> None:
        # D-14: skip bot's own outgoing messages
        if event.message.out:
            return
        # D-13: chat filter (the ONLY filter — no sender allowlist per R-14)
        if event.chat_id != self._target_chat_id:
            return

        text: str = event.text or ""
        if not text.strip():
            return

        source_message_id: int = event.message.id
        source_chat_id: int = event.chat_id
        received_at_unix: float = now_unix()

        # Step 1: parse
        result = parse_signal(text, allowed_expirations=self._allowed_expirations)
        if isinstance(result, ParseFailure):
            self._log_parse_failure(result, text, source_message_id)
            await self._notifier.on_parse_failure(raw_text=text, reason=result.reason)
            return

        # Step 2: compute trigger times + signal_id
        tz = self._config.tz()
        signal_date = signal_date_in_tz(received_at_unix, tz)
        trigger_unix_initial = hhmm_to_unix(
            result.trigger_hhmm,
            signal_date,
            tz,
        )
        trigger_unix_gale1 = trigger_unix_initial + result.expiration_seconds
        trigger_unix_gale2 = trigger_unix_initial + 2 * result.expiration_seconds

        # Step 3: time-window check (FR-2.3)
        if not is_within_window(trigger_unix_initial, received_at_unix):
            self._log_out_of_window(
                result.trigger_hhmm,
                trigger_unix_initial,
                received_at_unix,
                source_message_id,
            )
            return

        # Step 4: build the full Signal dataclass
        signal_id = derive_signal_id(result, signal_date=signal_date)
        signal = Signal(
            signal_id=signal_id,
            pair=result.pair,
            direction=result.direction,
            trigger_hhmm=result.trigger_hhmm,
            expiration_seconds=result.expiration_seconds,
            received_at_unix=received_at_unix,
            source_message_id=source_message_id,
            source_chat_id=source_chat_id,
            raw_text=text,
            trigger_unix_initial=trigger_unix_initial,
            trigger_unix_gale1=trigger_unix_gale1,
            trigger_unix_gale2=trigger_unix_gale2,
        )

        # Step 5: persist
        inserted = await self._state_store.upsert_signal(signal)
        if not inserted:
            _log.info(
                "duplicate signal, ignoring: signal_id=%s pair=%s trigger=%s",
                signal.signal_id,
                signal.pair,
                signal.trigger_hhmm,
            )
            return

        # Step 6: enqueue
        await self._queue.put(signal)

        # Step 7: pretty-print to stdout
        print(json.dumps(asdict(signal), indent=2, default=str))

    def _log_parse_failure(
        self,
        failure: ParseFailure,
        text: str,
        source_message_id: int,
    ) -> None:
        preview = text[:80].replace("\n", " ")
        self._parse_failures_logger.warning(
            "parse_failure: reason=%s message_id=%s preview=%r",
            failure.reason.value,
            source_message_id,
            preview,
        )

    def _log_out_of_window(
        self,
        trigger_hhmm: str,
        trigger_unix: float,
        now_unix_val: float,
        source_message_id: int,
    ) -> None:
        self._parse_failures_logger.warning(
            "parse_failure: reason=out_of_window message_id=%s trigger_hhmm=%s "
            "trigger_unix=%.3f now_unix=%.3f skew_sec=%.1f",
            source_message_id,
            trigger_hhmm,
            trigger_unix,
            now_unix_val,
            now_unix_val - trigger_unix,
        )
