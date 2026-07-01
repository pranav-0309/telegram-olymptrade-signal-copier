"""signal_copier — Telegram → MT5 signal copier (demo only, M13).

Top-level convenience re-exports. The canonical import path is the
submodule (e.g., `from signal_copier.broker import Broker`); the
top-level path (`from signal_copier import Broker`) is provided as a
shorthand for callers that prefer it.
"""

from signal_copier.broker.base import Broker, BrokerAuthError, UnsupportedPairError

__version__ = "0.2.0"

__all__ = ["Broker", "BrokerAuthError", "UnsupportedPairError", "__version__"]
