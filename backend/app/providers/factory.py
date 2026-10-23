from typing import AsyncIterator

from ..config import Settings
from .base import Candle, Heartbeat, MarketDataProvider, PriceTick, ProviderUnavailable
from .biquote import BiquoteProvider
from .oanda import OandaProvider


class UnconfiguredProvider(MarketDataProvider):
    name = "none"

    def __init__(self, reason: str):
        self.reason = reason

    async def candles(self, symbol: str, timeframe: str, count: int, since: int | None = None) -> list[Candle]:
        raise ProviderUnavailable(self.reason)

    async def price_stream(self, symbol: str) -> AsyncIterator[PriceTick | Heartbeat]:
        raise ProviderUnavailable(self.reason)
        yield  # pragma: no cover


def build_provider(s: Settings) -> MarketDataProvider:
    provider = s.market_data_provider or ("oanda" if s.oanda_configured else "")
    if provider == "biquote":
        return BiquoteProvider()
    if provider != "oanda":
        return UnconfiguredProvider("Select MARKET_DATA_PROVIDER='biquote' or 'oanda' in backend/.env. OANDA requires OANDA_API_KEY and OANDA_ACCOUNT_ID.")
    if not s.oanda_configured:
        return UnconfiguredProvider("OANDA was selected but is not configured. Set OANDA_API_KEY and OANDA_ACCOUNT_ID in backend/.env.")
    if not s.env_valid:
        return UnconfiguredProvider("OANDA_ENV must be 'practice' or 'live'.")
    return OandaProvider(s)
