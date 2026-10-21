import logging
import time
from datetime import datetime, timezone

import httpx

from .providers.base import Candle

log = logging.getLogger("cache")


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


class SupabaseCache:
    """Optional candle cache over Supabase PostgREST. Every failure degrades to 'no cache'."""

    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None,
                 clock=time.time, cooldown: float = 60.0):
        self._http = httpx.AsyncClient(
            base_url=f"{url}/rest/v1", timeout=httpx.Timeout(5.0), transport=transport,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
        self._clock = clock
        self._cooldown = cooldown
        self._down_until = 0.0

    def _usable(self) -> bool:
        return self._clock() >= self._down_until

    def _fail(self, what: str, e: Exception) -> None:
        log.warning("candle cache %s failed (%s); bypassing cache for %ss", what, type(e).__name__, self._cooldown)
        self._down_until = self._clock() + self._cooldown

    async def get(self, symbol: str, tf: str, count: int) -> list[Candle] | None:
        if not self._usable():
            return None
        try:
            r = await self._http.get("/candles", params={
                "select": "ts,open,high,low,close,volume", "instrument": f"eq.{symbol}", "timeframe": f"eq.{tf}",
                "order": "ts.desc", "limit": str(count)})
            r.raise_for_status()
            rows = r.json()
            out = [Candle(_epoch(x["ts"]), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]),
                          float(x["volume"]), True) for x in rows]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self._fail("read", e)
            return None
        return sorted(out, key=lambda c: c.time)

    async def upsert(self, symbol: str, tf: str, candles: list[Candle]) -> bool:
        rows = [{"instrument": symbol, "timeframe": tf, "ts": _iso(c.time), "open": c.open, "high": c.high,
                 "low": c.low, "close": c.close, "volume": c.volume}
                for c in candles if c.complete and not c.provisional]
        if not rows or not self._usable():
            return False
        try:
            r = await self._http.post("/candles", params={"on_conflict": "instrument,timeframe,ts"}, json=rows,
                                      headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            self._fail("write", e)
            return False
        return True
