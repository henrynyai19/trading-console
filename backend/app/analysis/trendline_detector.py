"""Trendlines through confirmed swings. Bullish lines join rising swing lows, bearish lines join falling swing highs.
A line is valid only if no close between its two anchors crosses it (beyond an ATR tolerance); it breaks on the first
later close beyond the tolerance."""
import hashlib
from dataclasses import dataclass
from itertools import combinations

import numpy as np

from .swing_detector import Swing


@dataclass(frozen=True)
class TLCfg:
    tol_atr: float = 0.3
    max_anchor_swings: int = 10
    min_bars: int = 8
    max_slope_atr: float = 0.75   # per bar
    per_direction: int = 2
    keep_broken_bars: int = 20


@dataclass(frozen=True)
class RawTrendline:
    id: str
    direction: str
    anchors: list[tuple[int, float]]
    start_time: int
    start_price: float
    end_time: int
    end_price: float
    slope: float
    touches: int
    strength: float
    status: str
    break_time: int | None
    created_at: int


def detect_trendlines(times: np.ndarray, c: np.ndarray, atr: np.ndarray, swings: list[Swing], tf: str,
                      cfg: TLCfg = TLCfg()) -> list[RawTrendline]:
    n = len(c)
    out: list[RawTrendline] = []
    for kind, direction in (("low", "bullish"), ("high", "bearish")):
        sw = [s for s in swings if s.kind == kind][-cfg.max_anchor_swings:]
        cands = []
        for a, b in combinations(sw, 2):
            if b.idx - a.idx < cfg.min_bars:
                continue
            if (direction == "bullish" and b.price <= a.price) or (direction == "bearish" and b.price >= a.price):
                continue
            slope = (b.price - a.price) / (b.idx - a.idx)
            tol = cfg.tol_atr * atr[b.idx]
            if abs(slope) > cfg.max_slope_atr * atr[b.idx]:
                continue
            line = lambda i: a.price + slope * (i - a.idx)  # noqa: E731
            sign = -1 if direction == "bullish" else 1       # closes must not cross to this side
            idxs = np.arange(a.idx, b.idx + 1)
            if np.any(sign * (c[idxs] - (a.price + slope * (idxs - a.idx))) > tol):
                continue
            end, brk = n - 1, None
            for i in range(b.idx + 1, n):
                if sign * (c[i] - line(i)) > tol:
                    brk, end = i, i
                    break
            hits = [s for s in sw if a.idx <= s.idx <= end and abs(s.price - line(s.idx)) <= tol]
            if len(hits) < 2:
                continue
            length = end - a.idx
            last_touch = max(s.idx for s in hits)
            strength = min(len(hits), 5) / 5 * 50 + min(length, 100) / 100 * 25 + 25 * max(0.0, 1 - (n - 1 - last_touch) / 200)
            if brk is not None:
                strength *= 0.5
                if (n - 1 - brk) > cfg.keep_broken_bars:
                    continue
            cands.append((strength, a, b, slope, hits, end, brk))
        used: set[int] = set()
        for strength, a, b, slope, hits, end, brk in sorted(cands, key=lambda x: -x[0]):
            if a.idx in used or b.idx in used or sum(1 for t in out if t.direction == direction) >= cfg.per_direction:
                continue
            used |= {a.idx, b.idx}
            tid = "tl-" + hashlib.sha1(f"{tf}|{direction}|{a.time}|{b.time}".encode()).hexdigest()[:10]
            out.append(RawTrendline(tid, direction, [(s.time, s.price) for s in hits], a.time, a.price, int(times[end]),
                                    a.price + slope * (end - a.idx), slope, len(hits), round(min(100.0, strength), 1),
                                    "broken" if brk is not None else "active", int(times[brk]) if brk is not None else None,
                                    b.confirmed_at))
    return sorted(out, key=lambda t: t.start_time)
