from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}

# Display symbols and provider aliases. NASDAQ and US30 are index CFDs.
INSTRUMENTS = {
    "XAUUSD": {"provider": "XAU_USD", "biquote": "XAUUSD", "name": "Gold", "digits": 2, "tick": 0.01},
    "USDJPY": {"provider": "USD_JPY", "biquote": "USDJPY", "name": "US Dollar / Yen", "digits": 3, "tick": 0.001},
    "NASDAQ": {"provider": "NAS100_USD", "biquote": "USTEC", "name": "US Tech 100 CFD", "digits": 1, "tick": 0.1},
    "US30": {"provider": "US30_USD", "biquote": "US30", "name": "Dow Jones CFD", "digits": 1, "tick": 0.1},
    "EURUSD": {"provider": "EUR_USD", "biquote": "EURUSD", "name": "Euro / US Dollar", "digits": 5, "tick": 0.00001},
    "GBPUSD": {"provider": "GBP_USD", "biquote": "GBPUSD", "name": "Pound / US Dollar", "digits": 5, "tick": 0.00001},
}


class ProviderUnavailable(Exception):
    """Credentials missing, rejected, or the vendor cannot be reached. Messages must never contain secrets."""


class InstrumentUnavailable(ProviderUnavailable):
    """The instrument is not offered to this account."""


@dataclass(frozen=True)
class Candle:
    time: int  # UTC epoch seconds, start of the candle
    open: float
    high: float
    low: float
    close: float
    volume: float
    complete: bool
    # True only when the candle was built from sampled stream prices and has no provider backing yet.
    provisional: bool = False


@dataclass(frozen=True)
class PriceTick:
    time: float
    bid: float
    ask: float
    tradeable: bool

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Heartbeat:
    time: float


class MarketDataProvider(ABC):
    name: str

    def symbol_code(self, symbol: str) -> str:
        return INSTRUMENTS[symbol]["provider"]

    @abstractmethod
    async def candles(self, symbol: str, timeframe: str, count: int, since: int | None = None) -> list[Candle]:
        """Ascending, unique candles. With `since`, everything from that UTC time to now (paged)."""

    @abstractmethod
    def price_stream(self, symbol: str) -> AsyncIterator[PriceTick | Heartbeat]:
        """Sampled price updates plus heartbeats. Not a complete tick feed."""

    async def available(self) -> set[str] | None:
        """Provider instrument names offered to this account, or None if unknown."""
        return None
