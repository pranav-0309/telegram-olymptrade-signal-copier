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


class BrokerAuthError(Exception):
    """Raised by Broker.place()/connect() when the broker rejects the token,
    the session is invalid, or the WS disconnects unexpectedly.

    Distinct from UnsupportedPairError: BrokerAuthError is an authentication
    or connectivity failure (the broker is reachable but the auth/session is
    not), whereas UnsupportedPairError is a missing-asset failure (the auth
    works but the requested pair doesn't exist on this account).

    The scheduler maps both to status='error', but BrokerAuthError triggers:
      1. notifier.on_olymp_disconnect() — only on disconnect mid-trade
      2. process exit non-zero — so Railway restarts the container

    S-11 (M10+) will wrap BrokerAuthError in a circuit-breaker counter so a
    bad token doesn't trigger an infinite restart loop. For v1, one bad
    token = manual investigation.
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

    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> Decimal:
        """Close `trade_id`, returning realized PnL. Added M13.1 — no caller yet.
        See docs/refactor.md §4.4 for design rationale.
        """

    async def close(self) -> None:
        """Tear down any connection. Idempotent. Called on shutdown and on
        unhandled broker errors so M6 can reconnect cleanly."""
