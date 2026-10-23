import asyncio
import hashlib
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from ..candle_utils import find_gaps, in_weekend_closure, weekend_overlap
from ..providers.base import INSTRUMENTS, TF_SECONDS, Candle, ProviderUnavailable
from ..service import MarketDataService
from .commentary import build_commentary
from .indicators import atr as atr_fn
from .models import (AnalysisResponse, Freshness, MTF, StructureEvent, SwingPoint, TFTrend, TrendInfo, Trendline,
                     TrendlinePoint, Zone)
from .multi_timeframe import combine, higher_timeframes, tf_trend
from .structure_analyzer import analyze_structure
from .support_resistance import ZoneCfg, build_zones, nearest
from .swing_detector import arrays, detect_swings
from .trend_detector import detect_trend
from .trendline_detector import TLCfg, detect_trendlines

log = logging.getLogger("analysis")


@dataclass(frozen=True)
class AnalysisConfig:
    left: int = 3
    right: int = 3
    min_swing_strength: float = 10.0
    atr_period: int = 14
    break_buffer_atr: float = 0.0
    min_candles: int = 60
    history_count: int = 600
    include_invalidated: bool = False
    stale_factor: float = 3.0
    zone: ZoneCfg = field(default_factory=ZoneCfg)
    trendline: TLCfg = field(default_factory=TLCfg)

    def public(self) -> dict:
        return {"left": self.left, "right": self.right, "min_swing_strength": self.min_swing_strength,
                "atr_period": self.atr_period, "min_candles": self.min_candles, "history_count": self.history_count}


def closed_candles(candles: list[Candle]) -> list[Candle]:
    """Official closed candles only, ascending, unique by time, sane OHLC. Developing/provisional candles never count."""
    seen: dict[int, Candle] = {}
    for c in candles:
        if not c.complete or c.provisional:
            continue
        vals = (c.open, c.high, c.low, c.close)
        if not all(math.isfinite(v) and v > 0 for v in vals) or c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close, c.high):
            continue
        seen[c.time] = c
    return [seen[t] for t in sorted(seen)]


def freshness_of(closed: list[Candle], tf: str, now: float, cfg: AnalysisConfig) -> Freshness:
    if not closed:
        return Freshness(status="unknown")
    step = TF_SECONDS[tf]
    last = closed[-1].time
    close_at = last + step
    age = max(0.0, now - close_at)
    trading_age = max(0.0, age - weekend_overlap(close_at, now))
    if in_weekend_closure(now):
        status = "market_closed"
    else:
        status = "fresh" if trading_age <= cfg.stale_factor * step else "stale"
    return Freshness(status=status, last_closed_time=last, last_close_at=close_at, age_seconds=age, trading_age_seconds=trading_age)


@dataclass
class Body:
    trend: dict | None
    swings: list[SwingPoint]
    zones: list[Zone]
    nearest_support: str | None
    nearest_resistance: str | None
    events: list[StructureEvent]
    trendlines: list[Trendline]
    structure_state: str
    warnings: list[str]


def compute_body(symbol: str, tf: str, closed: list[Candle], cfg: AnalysisConfig) -> Body:
    step = TF_SECONDS[tf]
    times, _, h, l, c = arrays(closed)
    a = atr_fn(h, l, c, cfg.atr_period)
    swings = detect_swings(closed, step, cfg.left, cfg.right, cfg.min_swing_strength, a)
    st = analyze_structure(times, c, a, swings, step, cfg.break_buffer_atr)
    zones = build_zones(times, h, l, c, a, swings, symbol, tf, step, cfg.zone, cfg.include_invalidated)
    sup, res = nearest(zones, float(c[-1]))
    lines = detect_trendlines(times, c, a, swings, tf, cfg.trendline)
    trend = detect_trend(c, a, swings, st)

    warnings = []
    gaps = find_gaps([int(t) for t in times], tf)
    if gaps:
        first = datetime.fromtimestamp(gaps[0][0], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        warnings.append(f"{len(gaps)} missing-candle gap(s) in the analysed window (first at {first}); swing windows count candles, not elapsed time.")
    if np.any((h - l) > 10 * a):
        warnings.append("Extreme-volatility candle(s) (range over 10x ATR) are present; zone widths and trend scores may be distorted.")

    return Body(
        trend=trend,
        swings=[SwingPoint(id=f"{s.kind}-{s.time}", kind=s.kind, label=s.label, price=s.price, time=s.time,
                           confirmed_at=s.confirmed_at, timeframe=tf, strength=s.strength) for s in swings],
        zones=[Zone(id=z.id, instrument=symbol, timeframe=tf, type=z.type, origin_type=z.origin_type, upper=z.upper,
                    lower=z.lower, strength=z.strength, touches=z.touches, start_time=z.start_time,
                    created_at=z.created_at, last_interaction=z.last_interaction, status=z.status,
                    role_reversed=z.role_reversed, role_reversal_time=z.role_reversal_time) for z in zones],
        nearest_support=sup, nearest_resistance=res,
        events=[StructureEvent(id=f"{e.kind}-{e.direction}-{e.break_time}", kind=e.kind, direction=e.direction, level=e.level,
                               broken_swing_time=e.broken_time, swing_confirmed_at=e.swing_confirmed_at,
                               break_time=e.break_time, confirmed_at=e.confirmed_at, instrument=symbol, timeframe=tf)
                for e in st.events],
        trendlines=[Trendline(id=t.id, timeframe=tf, direction=t.direction,
                              anchors=[TrendlinePoint(time=x, price=p) for x, p in t.anchors], start_time=t.start_time,
                              start_price=t.start_price, end_time=t.end_time, end_price=t.end_price, slope_per_bar=t.slope,
                              touches=t.touches, strength=t.strength, status=t.status, break_time=t.break_time,
                              created_at=t.created_at) for t in lines],
        structure_state={"bull": "bullish", "bear": "bearish", None: "undetermined"}[st.state], warnings=warnings)


@dataclass
class Core:
    status: str
    reason: str | None
    freshness: Freshness
    candles_used: int
    body: Body | None


def assemble(symbol: str, tf: str, closed: list[Candle], body: Body | None, now: float, cfg: AnalysisConfig) -> Core:
    fr = freshness_of(closed, tf, now, cfg)
    if len(closed) < cfg.min_candles:
        return Core("insufficient_data", f"Only {len(closed)} closed candles are available; at least {cfg.min_candles} are required.", fr, len(closed), None)
    if fr.status == "stale":
        return Core("stale", f"The latest closed {tf} candle is {fr.trading_age_seconds / 60:.0f} trading minutes old; analysis is withheld until data is current.", fr, len(closed), None)
    return Core("ok", None, fr, len(closed), body)


def analyze_candles(symbol: str, tf: str, candles: list[Candle], now: float, cfg: AnalysisConfig = AnalysisConfig()) -> Core:
    """Pure entry point (also used by replay tests): every result depends only on the candles given."""
    closed = closed_candles(candles)
    body = compute_body(symbol, tf, closed, cfg) if len(closed) >= cfg.min_candles else None
    return assemble(symbol, tf, closed, body, now, cfg)


class AnalysisService:
    def __init__(self, market: MarketDataService, clock=time.time, cfg: AnalysisConfig = AnalysisConfig(),
                 ttl: float = 5.0, max_cache: int = 64):
        self.market, self._clock, self.cfg, self.ttl, self._max = market, clock, cfg, ttl, max_cache
        self._fetched: dict[tuple, tuple[float, list[Candle]]] = {}
        self._bodies: OrderedDict[tuple, Body] = OrderedDict()
        self.computations = 0  # observable for tests

    async def _closed(self, symbol: str, tf: str, cfg: AnalysisConfig) -> list[Candle]:
        key, now = (symbol, tf, cfg.history_count), self._clock()
        hit = self._fetched.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        candles, _ = await self.market.history(symbol, tf, cfg.history_count)
        closed = closed_candles(candles)
        self._fetched[key] = (now, closed)
        return closed

    async def closed_history(self, symbol: str, tf: str, cfg: AnalysisConfig | None = None) -> list[Candle]:
        """Return the same official, closed-candle window used by analysis."""
        return await self._closed(symbol, tf, cfg or self.cfg)

    def _core(self, symbol: str, tf: str, closed: list[Candle], cfg: AnalysisConfig) -> Core:
        body = None
        if len(closed) >= cfg.min_candles:
            # A new completed candle changes (last time, count), which invalidates the cached computation.
            key = (symbol, tf, cfg, closed[-1].time, len(closed))
            body = self._bodies.get(key)
            if body is None:
                body = compute_body(symbol, tf, closed, cfg)
                self.computations += 1
                self._bodies[key] = body
                while len(self._bodies) > self._max:
                    self._bodies.popitem(last=False)
            else:
                self._bodies.move_to_end(key)
        return assemble(symbol, tf, closed, body, self._clock(), cfg)

    async def analyze(self, symbol: str, tf: str, cfg: AnalysisConfig | None = None) -> AnalysisResponse:
        cfg = cfg or self.cfg
        closed = await self._closed(symbol, tf, cfg)  # ProviderUnavailable propagates: the primary timeframe is required
        core = self._core(symbol, tf, closed, cfg)
        now = int(self._clock())

        mtf = None
        highers = higher_timeframes(tf)
        if core.status == "ok":
            async def one(h: str) -> dict:
                try:
                    hc = await self._closed(symbol, h, cfg)
                    c = self._core(symbol, h, hc, cfg)
                except ProviderUnavailable as e:
                    return tf_trend(h, None, str(e))
                if c.status != "ok" or c.body is None:
                    return tf_trend(h, None, c.reason)
                return tf_trend(h, c.body.trend)
            results = await asyncio.gather(*[one(h) for h in highers])
            primary = tf_trend(tf, core.body.trend if core.body else None)
            mtf = MTF(**combine(primary, list(results)))

        b = core.body if core.status == "ok" else None
        resp = AnalysisResponse(
            instrument=symbol, provider_symbol=self.market.provider.symbol_code(symbol), timeframe=tf, analysis_time=now,
            status=core.status, reason=core.reason, freshness=core.freshness, candles_used=core.candles_used,
            warnings=b.warnings if b else [], config=cfg.public(),
            trend=TrendInfo(**b.trend) if b and b.trend else None,
            swings=b.swings if b else [], zones=b.zones if b else [],
            nearest_support=b.nearest_support if b else None, nearest_resistance=b.nearest_resistance if b else None,
            events=b.events if b else [], trendlines=b.trendlines if b else [], mtf=mtf)
        if b and b.trend is None:
            resp.reason = "Fewer than two confirmed swing highs and lows: trend cannot be classified yet."
        resp.commentary = build_commentary(resp)
        return resp
