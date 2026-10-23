import asyncio
import logging
import random
import time
from dataclasses import dataclass, field, replace

import httpx

from .candle_utils import merge_candles
from .providers.base import INSTRUMENTS, TF_SECONDS, Candle, MarketDataProvider, PriceTick, ProviderUnavailable
from .service import MarketDataService

log = logging.getLogger("stream")
MAX_KEPT = 300


def candle_dict(c: Candle) -> dict:
    return {"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume, "complete": c.complete, "provisional": c.provisional}


class FeedState:
    """Developing candle for one (symbol, timeframe), built from official candles plus sampled prices."""

    def __init__(self, symbol: str, tf: str, tail: list[Candle], now: float = 0.0):
        self.symbol, self.tf, self.step = symbol, tf, TF_SECONDS[tf]
        self.candles: dict[int, Candle] = {}
        merge_candles(self.candles, tail)
        self.need_reconcile = False
        self.last_reconcile = now  # the tail was just fetched from the selected provider

    @property
    def developing(self) -> Candle | None:
        if not self.candles:
            return None
        last = self.candles[max(self.candles)]
        return None if last.complete and not last.provisional else last

    def tail(self, n: int = 50) -> list[Candle]:
        return [self.candles[t] for t in sorted(self.candles)][-n:]

    def _trim(self) -> None:
        for t in sorted(self.candles)[:-MAX_KEPT]:
            del self.candles[t]

    def apply_tick(self, price: float, t: float) -> list[Candle]:
        """Returns the candles that changed. Out-of-order (older than the developing candle) ticks are ignored."""
        dev = self.developing
        if dev is None or t < dev.time:
            return []
        if t >= dev.time + self.step:
            # Clock rollover: close by time (provisional) and open a provisional bucket; reconcile fixes both.
            start = dev.time + int((t - dev.time) // self.step) * self.step
            closed = replace(dev, complete=True, provisional=True)
            new = Candle(start, price, price, price, price, 0, False, True)
            self.candles[dev.time], self.candles[start] = closed, new
            self.need_reconcile = True
            self._trim()
            return [closed, new]
        upd = replace(dev, high=max(dev.high, price), low=min(dev.low, price), close=price)
        if upd == dev:
            return []
        self.candles[dev.time] = upd
        return [upd]

    def reconcile(self, official: list[Candle]) -> tuple[list[Candle], list[int]]:
        """Provider candles win. Provisional buckets that match no provider candle are dropped."""
        merge_candles(self.candles, official)
        times = {c.time for c in official}
        latest = max(times, default=0)
        drop = [t for t, c in self.candles.items() if c.provisional and t <= latest and t not in times]
        for t in drop:
            del self.candles[t]
        self.need_reconcile = not any(not c.complete for c in official)
        return official, drop


@dataclass(eq=False)
class Subscription:
    symbol: str
    tf: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))

    def push(self, msg: dict) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # slow consumer: drop the oldest message
        self.queue.put_nowait(msg)


@dataclass
class SymbolFeed:
    symbol: str
    subs: set = field(default_factory=set)
    states: dict = field(default_factory=dict)
    task: asyncio.Task | None = None
    upstream: str = "connecting"  # connecting | connected | reconnecting
    market_open: bool | None = None
    last_price_at: float | None = None
    last_event_at: float = 0.0
    had_connection: bool = False


class FeedHub:
    def __init__(self, service: MarketDataService, provider: MarketDataProvider, *, heartbeat_timeout: float = 15.0,
                 backoff_base: float = 1.0, backoff_max: float = 30.0, reconcile_interval: float = 60.0,
                 min_reconcile_gap: float = 5.0, sleep=asyncio.sleep, clock=time.time,
                 jitter=lambda: random.random() * 0.25):
        self.service, self.provider = service, provider
        self.heartbeat_timeout, self.backoff_base, self.backoff_max = heartbeat_timeout, backoff_base, backoff_max
        self.reconcile_interval, self.min_reconcile_gap = reconcile_interval, min_reconcile_gap
        self._sleep, self._clock, self._jitter = sleep, clock, jitter
        self._feeds: dict[str, SymbolFeed] = {}
        self._lock = asyncio.Lock()

    # ---- subscription management -------------------------------------------------
    async def subscribe(self, symbol: str, tf: str) -> tuple[Subscription, list[Candle]]:
        async with self._lock:
            feed = self._feeds.get(symbol)
            state = feed.states.get(tf) if feed else None
            if state is None:
                state = FeedState(symbol, tf, await self.service.tail(symbol, tf, 100), self._clock())  # may raise ProviderUnavailable
            feed = self._feeds.setdefault(symbol, SymbolFeed(symbol))
            feed.states[tf] = state
            sub = Subscription(symbol, tf)
            feed.subs.add(sub)
            if feed.task is None or feed.task.done():
                feed.task = asyncio.create_task(self._run(feed), name=f"feed-{symbol}")
            return sub, state.tail(50)

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            feed = self._feeds.get(sub.symbol)
            if not feed:
                return
            feed.subs.discard(sub)
            if not any(s.tf == sub.tf for s in feed.subs):
                feed.states.pop(sub.tf, None)
            if not feed.subs:
                task, feed.task = feed.task, None
                del self._feeds[sub.symbol]
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        for feed in list(self._feeds.values()):
            if feed.task:
                feed.task.cancel()
                await asyncio.gather(feed.task, return_exceptions=True)
        self._feeds.clear()

    def active_streams(self) -> int:
        return sum(1 for f in self._feeds.values() if f.task and not f.task.done())

    def status(self, symbol: str) -> dict:
        feed = self._feeds.get(symbol)
        now = self._clock()
        stale = feed is None or feed.upstream != "connected" or now - feed.last_event_at > self.heartbeat_timeout
        return {"type": "status", "symbol": symbol, "provider_symbol": self.provider.symbol_code(symbol),
                "market_open": feed.market_open if feed else None, "upstream": feed.upstream if feed else "closed",
                "stale": stale, "last_price_at": feed.last_price_at if feed else None, "server_time": now}

    def health_status(self) -> list[dict]:
        """Non-sensitive status for active symbol feeds; never includes prices or credentials."""
        return [{key: status[key] for key in ("symbol", "provider_symbol", "market_open", "upstream", "stale", "last_price_at")}
                for symbol in sorted(self._feeds)
                for status in (self.status(symbol),)]

    def _broadcast(self, feed: SymbolFeed, msg: dict, tf: str | None = None) -> None:
        for s in feed.subs:
            if tf is None or s.tf == tf:
                s.push(msg)

    # ---- upstream loop -------------------------------------------------------------
    async def _run(self, feed: SymbolFeed) -> None:
        attempt = 0
        while True:
            stream = self.provider.price_stream(feed.symbol).__aiter__()
            reason = "stream ended"
            try:
                first = True
                while True:
                    ev = await asyncio.wait_for(stream.__anext__(), timeout=self.heartbeat_timeout)
                    now = self._clock()
                    feed.last_event_at = now
                    if first:
                        first, attempt = False, 0
                        feed.upstream = "connected"
                        log.info("provider_stream_connected", extra={"event": "provider_stream_connected",
                                                                    "provider": self.provider.name,
                                                                    "symbol": feed.symbol})
                        if feed.had_connection:
                            await self._backfill(feed)
                        feed.had_connection = True
                        self._broadcast(feed, self.status(feed.symbol))
                    if isinstance(ev, PriceTick):
                        await self._on_price(feed, ev, now)
            except asyncio.CancelledError:
                raise
            except StopAsyncIteration:
                pass
            except asyncio.TimeoutError:
                reason = "heartbeat timeout"
            except (ProviderUnavailable, httpx.HTTPError) as e:
                reason = str(e) or type(e).__name__
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose:
                    await aclose()
            feed.upstream = "reconnecting"
            log.warning("provider_stream_reconnecting (%s)", reason,
                        extra={"event": "provider_stream_reconnecting", "provider": self.provider.name,
                               "symbol": feed.symbol, "attempt": attempt + 1})
            self._broadcast(feed, self.status(feed.symbol))
            delay = min(self.backoff_max, self.backoff_base * 2 ** attempt) + self._jitter()
            attempt += 1
            await self._sleep(delay)

    async def _on_price(self, feed: SymbolFeed, tick: PriceTick, now: float) -> None:
        feed.last_price_at = tick.time
        if feed.market_open != tick.tradeable:
            feed.market_open = tick.tradeable
            self._broadcast(feed, self.status(feed.symbol))
        for tf, state in list(feed.states.items()):
            for c in state.apply_tick(tick.mid, tick.time):
                self._broadcast(feed, {"type": "candle", "symbol": feed.symbol, "timeframe": tf, "candle": candle_dict(c)}, tf)
            due = now - state.last_reconcile
            if (state.need_reconcile and due >= self.min_reconcile_gap) or due >= self.reconcile_interval:
                await self._reconcile(feed, state)

    async def _reconcile(self, feed: SymbolFeed, state: FeedState, backfill: bool = False) -> None:
        state.last_reconcile = self._clock()
        try:
            if backfill:
                last_official = max((t for t, c in state.candles.items() if c.complete and not c.provisional), default=None)
                official = await self.service.since(feed.symbol, state.tf, last_official) if last_official else \
                    await self.service.tail(feed.symbol, state.tf, 100)
            else:
                official = await self.service.tail(feed.symbol, state.tf, 5)
        except ProviderUnavailable as e:
            log.warning("market_data_reconcile_failed (%s)", e,
                        extra={"event": "market_data_reconcile_failed", "provider": self.provider.name,
                               "symbol": feed.symbol, "timeframe": state.tf})
            return
        official, drop = state.reconcile(official)
        if backfill:
            log.info("market_data_backfill_complete", extra={"event": "market_data_backfill_complete",
                                                              "provider": self.provider.name,
                                                              "symbol": feed.symbol, "timeframe": state.tf,
                                                              "candle_count": len(official)})
        self._broadcast(feed, {"type": "reconcile", "symbol": feed.symbol, "timeframe": state.tf,
                               "candles": [candle_dict(c) for c in official], "drop": drop}, state.tf)

    async def _backfill(self, feed: SymbolFeed) -> None:
        for state in list(feed.states.values()):
            await self._reconcile(feed, state, backfill=True)
