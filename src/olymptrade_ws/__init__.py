# olymptrade_ws/__init__.py
# Expose main classes for easy import
from .api.balance import BalanceAPI
from .api.market import MarketAPI
from .api.trade import PairUnavailableError, TradeAPI
from .core.client import OlympTradeClient as CoreOlympTradeClient
from .main import OlympTradeClient

__all__ = [
    "OlympTradeClient",
    "CoreOlympTradeClient",
    "BalanceAPI",
    "MarketAPI",
    "TradeAPI",
    "PairUnavailableError",
]
