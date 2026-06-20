from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


class UnsupportedPairError(Exception):
    """Raised by Broker.place() when the signal's pair is not available on the broker.

    The state machine catches this and marks the signal status='error'
    with error_reason='unsupported_pair' (PRD §10). M8's OlympTradeBroker
    is the canonical raiser; M3's DryRunBroker never raises this.
    """


@runtime_checkable
class Broker(Protocol):
    """Broker-agnostic trading surface used by the scheduler (M6) and state
    machine (M2). Two concrete implementations exist in v1:

      - DryRunBroker       (M3, default for v1, FR-6.5: DRY_RUN=true)
      - OlympTradeBroker   (M8, wraps the vendored olymptrade_ws client)

    All methods are async because real brokers are I/O-bound (M8). M3's
    DryRunBroker is also async to keep the Protocol uniform; tests use
    pytest-asyncio (asyncio_mode="auto", already configured in M0).
    """

    async def connect(self) -> None:
        """Establish any required connection (Telethon session, WS handshake,
        asset-map fetch). Idempotent — second call is a no-op."""

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        """Submit a trade for `signal` at `stage` for `amount` USD.

        Returns the broker's trade_id, which the scheduler uses to identify
        the trade in `wait_result` and which M6 persists as `stages.trade_id`.

        Raises UnsupportedPairError if `signal.pair` is not available on this
        broker. The state machine catches this and ends the cascade with
        status='error' (PRD §10).
        """

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        """Block until the broker reports a terminal result for `trade_id`,
        or until `timeout` seconds elapse.

        Returns one of M2's StageResult literals: 'win' | 'loss' | 'tie'
        | 'timeout' | 'error'. The 'timeout' literal here means the
        *broker-reporting* timeout — distinct from the per-stage
        expiration-grace timeout in PRD FR-5.3, which M6 owns.
        """

    async def close(self) -> None:
        """Tear down any connection. Idempotent. Called on shutdown and on
        unhandled broker errors so M6 can reconnect cleanly."""
