"""Broker abstraction layer.

Provides the Broker Protocol (M3) and concrete implementations:
  - DryRunBroker      (M3, default for v1)
  - OlympTradeBroker  (M8, wraps vendored olymptrade_ws)
"""

from signal_copier.broker.base import Broker, BrokerAuthError, UnsupportedPairError

__all__ = ["Broker", "BrokerAuthError", "UnsupportedPairError"]
