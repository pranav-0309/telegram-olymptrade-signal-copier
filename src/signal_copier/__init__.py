"""signal_copier — Telegram → OlympTrade signal copier (demo only, v1).

Top-level convenience re-exports. The canonical import path is the
submodule (e.g., `from signal_copier.broker import Broker`); the
top-level path (`from signal_copier import Broker`) is provided as a
shorthand for callers that prefer it.
"""

from signal_copier.broker.base import Broker, UnsupportedPairError

__all__ = ["Broker", "UnsupportedPairError"]
