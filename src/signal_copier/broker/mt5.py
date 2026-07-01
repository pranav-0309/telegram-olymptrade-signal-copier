"""MT5 broker (M13.2). M13.1 ships a stub so __main__.py can import Mt5Broker.

Real implementation lands in M13.2 (docs/refactor.md §4.3 + §4.5):
  - mt5.initialize() in asyncio.to_thread
  - place() via mt5.order_send()
  - wait_result() via mt5.positions_get + order poll
  - close_position() via mt5.Close() — returns position.profit (Decimal)
  - reconnect via broker/reconnect.py (M13.2)

Until M13.2, every method raises NotImplementedError so a DRY_RUN=false
boot fails fast with an explicit error rather than a half-built session.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)


class Mt5Broker:
    """M13.2 implementation. M13.1 ships a stub; see module docstring."""

    def __init__(
        self,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None,
        notifier: object,  # Notifier — cyclic import avoidance; M13.2 narrows to Notifier
    ) -> None:
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._notifier = notifier
        _log.warning(
            "Mt5Broker: stub class (M13.1). Real impl lands in M13.2. "
            "Do not deploy with DRY_RUN=false."
        )

    async def connect(self) -> None:
        raise NotImplementedError(
            "Mt5Broker.connect() lands in M13.2 (docs/refactor.md §4.3 + §5). "
            "Until then, set DRY_RUN=true."
        )

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        raise NotImplementedError("Mt5Broker.place() lands in M13.2. Until then, set DRY_RUN=true.")

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        raise NotImplementedError(
            "Mt5Broker.wait_result() lands in M13.2. Until then, set DRY_RUN=true."
        )

    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> Decimal:
        raise NotImplementedError(
            "Mt5Broker.close_position() lands in M13.2. Until then, set DRY_RUN=true."
        )

    async def close(self) -> None:
        raise NotImplementedError("Mt5Broker.close() lands in M13.2.")
