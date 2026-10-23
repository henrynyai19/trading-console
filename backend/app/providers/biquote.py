"""Anonymous Biquote REST candles and SignalR price stream adapter."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from urllib.parse import quote

import httpx
import websockets

from .base import INSTRUMENTS, TF_SECONDS, Candle, Heartbeat, MarketDataProvider, PriceTick, ProviderUnavailable

log = logging.getLogger("biquote")
REST_URL = "https://biquote.io"
WS_URL = "wss://biquote.io/hubs/tick"
INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}
MAX_PAGE = 1000
MAX_PAGES = 20


def parse_time(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Provider timestamps are normally ISO UTC, but accept epoch seconds/milliseconds too.
        n = float(value)
        return int(n / 1000 if n > 100_000_000_000 else n)
    raw = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return int(dt.timestamp())


def parse_bar(raw: dict, now: float) -> Candle:
    try:
        stamp = parse_time(raw["openTime"])
        o, h, l, c = (float(raw[k]) for k in ("open", "high", "low", "close"))
        volume = float(raw.get("tickVolume", raw.get("volume", 0)) or 0)
        is_open = raw["isOpen"]
    except (KeyError, TypeError, ValueError, OverflowError) as e:
        raise ValueError("malformed bar") from e
    if not isinstance(is_open, bool):
        raise ValueError("bar is missing its open/closed flag")
    if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) or h < max(o, c, l) or l > min(o, c, h):
        raise ValueError("invalid OHLC values")
    if stamp < 946684800 or stamp > now + 120:
        raise ValueError("bar time is outside the valid range")
    return Candle(stamp, o, h, l, c, max(volume, 0), not is_open)


def aggregate_weekly(daily: list[Candle], now: float) -> list[Candle]:
    groups: dict[int, list[Candle]] = {}
    for candle in daily:
        dt = datetime.fromtimestamp(candle.time, timezone.utc)
        monday = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dt.weekday())
        groups.setdefault(int(monday.timestamp()), []).append(candle)
    out: list[Candle] = []
    for start, rows in sorted(groups.items()):
        rows.sort(key=lambda x: x.time)
        complete = start + TF_SECONDS["1w"] <= now and all(x.complete for x in rows)
        out.append(Candle(start, rows[0].open, max(x.high for x in rows), min(x.low for x in rows),
                          rows[-1].close, sum(x.volume for x in rows), complete))
    return out


class BiquoteProvider(MarketDataProvider):
    name = "biquote"

    def __init__(self, client: httpx.AsyncClient | None = None, ws_connector=None, clock=time.time):
        self._http = client or httpx.AsyncClient(base_url=REST_URL, timeout=httpx.Timeout(20.0))
        self._owns_http = client is None
        self._ws_connect = ws_connector or websockets.connect
        self._clock = clock
        self._avail: set[str] | None = None
        self._avail_at = 0.0

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def symbol_code(self, symbol: str) -> str:
        if symbol not in INSTRUMENTS:
            raise ProviderUnavailable(f"Unknown instrument {symbol}.")
        return INSTRUMENTS[symbol]["biquote"]

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as e:
            log.warning("market_data_request_failed", extra={
                "event": "market_data_request_failed", "provider": self.name,
                "error_type": type(e).__name__, "failure_kind": "timeout",
            })
            raise ProviderUnavailable("Biquote market-data request timed out. Check provider availability and retry.") from e
        except httpx.ConnectError as e:
            log.warning("market_data_request_failed", extra={
                "event": "market_data_request_failed", "provider": self.name,
                "error_type": type(e).__name__, "failure_kind": "connect",
            })
            raise ProviderUnavailable(
                "Cannot connect to Biquote. Check backend outbound network access, DNS, or proxy settings."
            ) from e
        except httpx.HTTPError as e:
            log.warning("market_data_request_failed", extra={
                "event": "market_data_request_failed", "provider": self.name,
                "error_type": type(e).__name__, "failure_kind": "http_client",
            })
            raise ProviderUnavailable("Biquote market-data request failed. Check backend network access and retry.") from e
        if response.status_code == 404:
            raise ProviderUnavailable("Biquote has no recent quote history for this instrument.")
        if response.status_code == 429:
            raise ProviderUnavailable("Biquote rate limit reached; retry after its limit resets.")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"Biquote is temporarily unavailable (HTTP {response.status_code}).")
        if response.status_code != 200:
            raise ProviderUnavailable(f"Biquote returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except ValueError as e:
            raise ProviderUnavailable("Biquote returned unreadable market data.") from e
        if isinstance(body, dict) and body.get("error"):
            raise ProviderUnavailable("Biquote rejected the market-data request.")
        return body

    async def available(self) -> set[str] | None:
        if self._avail is not None and self._clock() - self._avail_at < 300:
            return self._avail
        body = await self._get("/api/active")
        if not isinstance(body, list):
            raise ProviderUnavailable("Biquote returned an unreadable active-instrument list.")
        self._avail = {str(item.get("name", "")) for item in body if isinstance(item, dict)}
        self._avail_at = self._clock()
        return self._avail

    async def _page(self, symbol: str, timeframe: str, cursor: int | None) -> list[Candle]:
        interval = INTERVALS[timeframe]
        params: dict[str, object] = {"interval": interval, "limit": MAX_PAGE}
        if cursor is not None:
            params["to"] = datetime.fromtimestamp(cursor, timezone.utc).isoformat().replace("+00:00", "Z")
        body = await self._get(f"/api/{self.symbol_code(symbol)}/ohlc", params)
        try:
            raw = body["bars"] if isinstance(body, dict) else None
            if not isinstance(raw, list):
                raise ValueError("no bars array")
            parsed = [parse_bar(row, self._clock()) for row in raw if isinstance(row, dict)]
        except (KeyError, TypeError, ValueError) as e:
            raise ProviderUnavailable("Biquote returned malformed candle data.") from e
        return sorted({c.time: c for c in parsed}.values(), key=lambda c: c.time)

    async def candles(self, symbol: str, timeframe: str, count: int, since: int | None = None) -> list[Candle]:
        if timeframe not in (*INTERVALS, "1w"):
            raise ProviderUnavailable(f"Biquote does not support the {timeframe} timeframe.")
        provider_tf = "1d" if timeframe == "1w" else timeframe
        # Pull backward in bounded API pages. The service asks for at most 5,000 bars.
        wanted = min(max(count, 1) * (7 if timeframe == "1w" else 1), 5000) if since is None else 5000
        rows: dict[int, Candle] = {}
        cursor: int | None = None
        for _ in range(MAX_PAGES):
            page = await self._page(symbol, provider_tf, cursor)
            if not page:
                break
            old_count = len(rows)
            rows.update({c.time: c for c in page})
            oldest = min(c.time for c in page)
            if since is not None and oldest <= since:
                break
            if since is None and len(rows) >= wanted:
                break
            if len(rows) == old_count:
                break
            cursor = oldest - 1
            if len(page) < MAX_PAGE:
                break
        result = sorted(rows.values(), key=lambda c: c.time)
        if since is not None:
            result = [c for c in result if c.time >= since]
        if timeframe == "1w":
            result = aggregate_weekly(result, self._clock())
        if since is None:
            return result[-max(count, 1):]
        return result

    async def price_stream(self, symbol: str) -> AsyncIterator[PriceTick | Heartbeat]:
        code = self.symbol_code(symbol)
        negotiation = await self._http.post("/hubs/tick/negotiate", params={"negotiateVersion": "1"})
        if negotiation.status_code != 200:
            raise ProviderUnavailable(f"Biquote stream negotiation failed (HTTP {negotiation.status_code}).")
        try:
            token = negotiation.json()["connectionToken"]
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderUnavailable("Biquote returned an invalid stream negotiation response.") from e
        url = f"{WS_URL}?id={quote(str(token), safe='')}"
        try:
            async with self._ws_connect(url, open_timeout=15, ping_interval=20, ping_timeout=20) as socket:
                await socket.send('{"protocol":"json","version":1}\x1e')
                handshake = await asyncio.wait_for(socket.recv(), timeout=10)
                if isinstance(handshake, bytes):
                    handshake = handshake.decode("utf-8")
                if handshake.strip("\x1e") not in ("", "{}"):
                    raise ProviderUnavailable("Biquote rejected the stream protocol handshake.")
                await socket.send(json.dumps({"type": 1, "target": "Subscribe", "arguments": [[code]]}) + "\x1e")
                async for frame in socket:
                    if isinstance(frame, bytes):
                        frame = frame.decode("utf-8")
                    for record in frame.split("\x1e"):
                        if not record.strip():
                            continue
                        try:
                            message = json.loads(record)
                        except ValueError:
                            log.warning("dropped malformed Biquote stream message")
                            continue
                        kind = message.get("type")
                        if kind == 6:
                            yield Heartbeat(self._clock())
                            continue
                        if kind == 7:
                            raise ProviderUnavailable("Biquote ended the price stream.")
                        if kind != 1 or message.get("target") != "ReceiveTick":
                            continue
                        args = message.get("arguments") or []
                        if not args or not isinstance(args[0], dict):
                            continue
                        tick = args[0]
                        try:
                            bid, ask = float(tick["bid"]), float(tick["ask"])
                            stamp = parse_time(tick.get("timestamp", tick.get("time")))
                            stale = bool(tick.get("stale", False))
                            open_state = str(tick.get("marketState", "")).lower() == "open"
                        except (KeyError, TypeError, ValueError, OverflowError):
                            log.warning("dropped malformed Biquote tick")
                            continue
                        if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
                            continue
                        yield PriceTick(stamp, bid, ask, open_state and not stale)
        except asyncio.CancelledError:
            raise
        except ProviderUnavailable:
            raise
        except Exception as e:
            raise ProviderUnavailable(f"Biquote price stream failed ({type(e).__name__}).") from e
