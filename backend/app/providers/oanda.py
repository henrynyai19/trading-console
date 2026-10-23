import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime
from typing import AsyncIterator

import httpx

from ..config import Settings
from .base import (INSTRUMENTS, TF_SECONDS, Candle, Heartbeat, InstrumentUnavailable, MarketDataProvider,
                   PriceTick, ProviderUnavailable)

log = logging.getLogger("oanda")

GRANULARITY = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D", "1w": "W"}
MAX_COUNT = 5000  # OANDA's per-request candle limit
MAX_PAGES = 20
MIN_TS = 946684800  # 2000-01-01


def parse_time(v) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    s = s.replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return d.timestamp()


def parse_candle(raw: dict, now: float | None = None) -> Candle:
    """Validate one OANDA candle. Raises ValueError for anything malformed or implausible."""
    now = time.time() if now is None else now
    try:
        mid = raw["mid"]
        t = parse_time(raw["time"])
        o, h, l, c = (float(mid[k]) for k in "ohlc")
        vol = int(raw.get("volume", 0))
        complete = raw["complete"]
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"malformed candle: {type(e).__name__}") from e
    if not isinstance(complete, bool):
        raise ValueError("malformed candle: complete flag")
    if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
        raise ValueError("non-positive or non-finite price")
    if h < max(o, c, l) or l > min(o, c, h):
        raise ValueError("inconsistent OHLC")
    if not (MIN_TS <= t <= now + 120):
        raise ValueError("timestamp out of range")
    return Candle(int(t), o, h, l, c, vol, complete)


def parse_candles(payload: dict, now: float | None = None) -> list[Candle]:
    """Ascending, de-duplicated candles; invalid ones are dropped and logged."""
    raw = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise ValueError("response has no candles list")
    out: dict[int, Candle] = {}
    dropped = 0
    for r in raw:
        try:
            c = parse_candle(r, now)
        except ValueError:
            dropped += 1
            continue
        if c.time not in out or c.complete:
            out[c.time] = c
    if dropped:
        log.warning("dropped %d invalid candle(s)", dropped)
    return [out[t] for t in sorted(out)]


def parse_stream_line(line: str) -> PriceTick | Heartbeat | None:
    try:
        d = json.loads(line)
        kind = d.get("type")
        if kind == "HEARTBEAT":
            return Heartbeat(parse_time(d["time"]))
        if kind != "PRICE":
            return None
        bid = float((d.get("bids") or [{"price": d["closeoutBid"]}])[0]["price"])
        ask = float((d.get("asks") or [{"price": d["closeoutAsk"]}])[0]["price"])
        t = parse_time(d["time"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        raise ValueError(f"malformed stream message: {type(e).__name__}") from e
    if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
        raise ValueError("invalid bid/ask")
    return PriceTick(t, bid, ask, bool(d.get("tradeable", False)))


class OandaProvider(MarketDataProvider):
    name = "oanda"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 sleep=asyncio.sleep, clock=time.time):
        self._s = settings
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._headers = {"Authorization": f"Bearer {settings.oanda_api_key}", "Accept-Datetime-Format": "UNIX"}
        self._rest = httpx.AsyncClient(base_url=settings.rest_host, headers=self._headers,
                                       timeout=httpx.Timeout(15.0), transport=transport)
        self._avail: set[str] | None = None
        self._avail_at = 0.0

    async def aclose(self) -> None:
        await self._rest.aclose()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        last = "unknown"
        for attempt in range(4):
            try:
                r = await self._rest.get(path, params=params)
            except httpx.HTTPError as e:
                last = f"network error ({type(e).__name__})"
                await self._sleep(min(2 ** attempt, 8))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    raise ProviderUnavailable("OANDA returned an unreadable response.")
            if r.status_code in (401, 403):
                raise ProviderUnavailable("OANDA rejected the credentials. Check OANDA_API_KEY, OANDA_ACCOUNT_ID and OANDA_ENV.")
            if r.status_code == 429:
                last = "rate limited"
                try:
                    delay = float(r.headers.get("Retry-After", 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                await self._sleep(min(max(delay, 0), 30))
                continue
            if r.status_code >= 500:
                last = f"server error {r.status_code}"
                await self._sleep(min(2 ** attempt, 8))
                continue
            raise ProviderUnavailable(f"OANDA returned HTTP {r.status_code}.")
        raise ProviderUnavailable(f"OANDA request failed after retries ({last}).")

    async def available(self) -> set[str] | None:
        if self._avail is not None and self._clock() - self._avail_at < 3600:
            return self._avail
        data = await self._get(f"/v3/accounts/{self._s.oanda_account_id}/instruments")
        try:
            self._avail = {i["name"] for i in data["instruments"]}
        except (KeyError, TypeError):
            raise ProviderUnavailable("OANDA returned an unreadable instrument list.")
        self._avail_at = self._clock()
        return self._avail

    async def _instrument(self, symbol: str) -> str:
        if symbol not in INSTRUMENTS:
            raise InstrumentUnavailable(f"Unknown symbol {symbol}.")
        inst = INSTRUMENTS[symbol]["provider"]
        avail = await self.available()
        if avail is not None and inst not in avail:
            raise InstrumentUnavailable(f"{inst} is not available for this OANDA account.")
        return inst

    async def candles(self, symbol: str, timeframe: str, count: int, since: int | None = None) -> list[Candle]:
        if timeframe not in GRANULARITY:
            raise ProviderUnavailable(f"Unsupported timeframe {timeframe}.")
        inst = await self._instrument(symbol)
        path = f"/v3/instruments/{inst}/candles"
        base = {"price": "M", "granularity": GRANULARITY[timeframe], "smooth": "false"}
        now = self._clock()
        out: dict[int, Candle] = {}

        async def page(extra: dict) -> list[Candle]:
            try:
                return parse_candles(await self._get(path, {**base, **extra}), now)
            except ValueError as e:
                raise ProviderUnavailable(f"OANDA returned malformed candle data ({e}).")

        if since is None:
            remaining, to = max(count, 1), None
            for _ in range(MAX_PAGES):
                n = min(remaining + (1 if to is not None else 0), MAX_COUNT)  # `to` is inclusive: re-returns the boundary candle
                got = await page({"count": n, **({"to": to} if to is not None else {})})
                new = [c for c in got if c.time not in out]
                if not new:
                    break
                out.update({c.time: c for c in new})
                remaining -= len(new)
                to = min(c.time for c in new)
                if remaining <= 0 or len(got) < n:
                    break
        else:
            cursor = since
            for _ in range(MAX_PAGES):
                got = await page({"from": cursor, "count": MAX_COUNT})
                new = [c for c in got if c.time not in out or (c.complete and not out[c.time].complete)]
                if not new:
                    break
                out.update({c.time: c for c in new})
                cursor = max(c.time for c in new)
                if len(got) < MAX_COUNT:
                    break
        return [out[t] for t in sorted(out)]

    async def price_stream(self, symbol: str) -> AsyncIterator[PriceTick | Heartbeat]:
        inst = await self._instrument(symbol)
        async with httpx.AsyncClient(base_url=self._s.stream_host, headers=self._headers,
                                     timeout=httpx.Timeout(10.0, read=20.0), transport=self._transport) as c:
            async with c.stream("GET", f"/v3/accounts/{self._s.oanda_account_id}/pricing/stream",
                                params={"instruments": inst}) as r:
                if r.status_code in (401, 403):
                    raise ProviderUnavailable("OANDA rejected the credentials for the price stream.")
                if r.status_code != 200:
                    raise ProviderUnavailable(f"OANDA price stream returned HTTP {r.status_code}.")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        ev = parse_stream_line(line)
                    except ValueError:
                        log.warning("dropped malformed stream message")
                        continue
                    if ev is not None:
                        yield ev
