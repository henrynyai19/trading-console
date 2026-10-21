import asyncio

import httpx

from app.providers.base import Candle, MarketDataProvider, ProviderUnavailable

BASE = 1_700_000_040  # a Tuesday, divisible by 60
NOW = 1_800_000_000


def mk(i: int, step: int = 60, complete: bool = True, base: int = BASE, **kw) -> Candle:
    p = 1.0 + i * 0.001
    return Candle(base + i * step, kw.get("o", p), kw.get("h", p + 0.002), kw.get("l", p - 0.002), kw.get("c", p + 0.001),
                  10, complete)


def raw(c: Candle) -> dict:
    return {"time": f"{c.time}.000000000", "volume": int(c.volume), "complete": c.complete,
            "mid": {"o": str(c.open), "h": str(c.high), "l": str(c.low), "c": str(c.close)}}


async def until(pred, timeout: float = 2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


class FakeProvider(MarketDataProvider):
    """Scripted provider. A stream script is a list of: events, exceptions, callables, 'hang' or ('sleep', s)."""
    name = "fake"

    def __init__(self, data=None, scripts=None):
        self.data = data or {}
        self.scripts = list(scripts or [])
        self.calls: list[tuple] = []
        self.fail = False
        self.streams_opened = 0
        self.streams_closed = 0

    async def candles(self, symbol, timeframe, count, since=None):
        self.calls.append((symbol, timeframe, count, since))
        if self.fail:
            raise ProviderUnavailable("fake failure")
        d = self.data.get((symbol, timeframe), [])
        if since is not None:
            return [c for c in d if c.time >= since]
        return d[-count:] if count else list(d)

    async def price_stream(self, symbol):
        self.streams_opened += 1
        script = self.scripts.pop(0) if self.scripts else ["hang"]
        try:
            for item in script:
                if isinstance(item, Exception):
                    raise item
                if item == "hang":
                    await asyncio.Event().wait()
                elif isinstance(item, tuple) and item[0] == "sleep":
                    await asyncio.sleep(item[1])
                elif callable(item):
                    item()
                else:
                    yield item
        finally:
            self.streams_closed += 1


def mock_client_transport(handler):
    return httpx.MockTransport(handler)
