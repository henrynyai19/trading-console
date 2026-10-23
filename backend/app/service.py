import logging
import time

from .cache import SupabaseCache
from .candle_utils import find_gaps, is_fresh, merge_candles
from .providers.base import TF_SECONDS, Candle, MarketDataProvider

log = logging.getLogger("service")


class MarketDataService:
    """The selected provider is authoritative; a cache never replaces a failed provider."""

    def __init__(self, provider: MarketDataProvider, cache: SupabaseCache | None = None, clock=time.time):
        self.provider, self.cache, self._clock = provider, cache, clock
        self._known_gaps: set[tuple[str, str, int]] = set()  # gaps the provider could not fill (market closures)

    async def _store(self, symbol: str, tf: str, candles: list[Candle]) -> None:
        if self.cache:
            await self.cache.upsert(symbol, tf, candles)

    async def tail(self, symbol: str, tf: str, n: int) -> list[Candle]:
        got = await self.provider.candles(symbol, tf, n)
        await self._store(symbol, tf, got)
        return got

    async def since(self, symbol: str, tf: str, since: int) -> list[Candle]:
        got = await self.provider.candles(symbol, tf, 0, since=since)
        await self._store(symbol, tf, got)
        return got

    async def history(self, symbol: str, tf: str, count: int) -> tuple[list[Candle], str]:
        """Returns (ascending unique candles, source). Raises ProviderUnavailable if the feed fails."""
        step = TF_SECONDS[tf]
        cached = await self.cache.get(symbol, tf, count) if self.cache else None
        # the developing candle is never cached, so N-1 complete rows satisfy a request for N
        if not cached or len(cached) < count - 1 or not is_fresh(cached[-1].time, tf, self._clock()):
            got = await self.provider.candles(symbol, tf, count)
            await self._store(symbol, tf, got)
            return got[-count:] if count else got, self.provider.name

        store: dict[int, Candle] = {}
        merge_candles(store, cached)
        fresh = await self.provider.candles(symbol, tf, 0, since=cached[-1].time)
        merge_candles(store, fresh)
        await self._store(symbol, tf, fresh)

        gaps = [g for g in find_gaps(sorted(store), tf) if (symbol, tf, g[0]) not in self._known_gaps]
        if gaps:
            filled = await self.provider.candles(symbol, tf, 0, since=gaps[0][0] - step)
            merge_candles(store, filled)
            await self._store(symbol, tf, filled)
            self._known_gaps.update((symbol, tf, g[0]) for g in gaps)  # do not refetch closures forever
        return [store[t] for t in sorted(store)][-count:], f"cache+{self.provider.name}"
