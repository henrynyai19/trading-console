"""Support/resistance zones built only from confirmed swings and closed candles.

1. Cluster all confirmed swing prices (highs and lows together) with ATR-scaled tolerances, so the same code works for
   gold at 2000, USDJPY at 150 and EURUSD at 1.1.
2. Pad each cluster by a fraction of ATR, enforce a minimum width, merge overlapping zones.
3. Count rejection touches: an excursion into the zone (consecutive overlapping candles) that exits on the side it
   entered from. Excursions that exit the other side are breaks, not touches.
4. Role: a close beyond the zone (by a buffer) flips resistance <-> support. A second flip means price has accepted both
   sides: the zone is invalidated.
Zones are live objects: boundaries and strength may change as new swings join a cluster. Swings and structure events,
by contrast, never change once confirmed.
"""
import hashlib
from dataclasses import dataclass

import numpy as np

from .swing_detector import Swing

TOUCH_GAP = 3  # candles: overlapping candles closer than this count as one touch


@dataclass(frozen=True)
class ZoneCfg:
    cluster_atr: float = 0.5
    span_atr: float = 1.0
    pad_atr: float = 0.1
    min_width_atr: float = 0.3
    break_buffer_atr: float = 0.1
    min_touches: int = 2
    max_zones: int = 8


@dataclass(frozen=True)
class RawZone:
    id: str
    type: str
    origin_type: str
    lower: float
    upper: float
    strength: float
    touches: int
    start_time: int
    created_at: int
    last_interaction: int
    status: str
    role_reversed: bool
    role_reversal_time: int | None


def _cluster(swings: list[Swing], tol: float, span: float) -> list[list[Swing]]:
    out: list[list[Swing]] = []
    for s in sorted(swings, key=lambda s: s.price):
        if out and s.price - out[-1][-1].price <= tol and s.price - out[-1][0].price <= span:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def _merge_overlaps(boxes: list[tuple[float, float, list[Swing]]]) -> list[tuple[float, float, list[Swing]]]:
    boxes.sort(key=lambda b: b[0])
    merged = []
    for lo, hi, sw in boxes:
        if merged and lo <= merged[-1][1]:
            plo, phi, psw = merged[-1]
            merged[-1] = (plo, max(phi, hi), psw + sw)
        else:
            merged.append((lo, hi, sw))
    return merged


def build_zones(times: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray, swings: list[Swing],
                symbol: str, tf: str, step: int, cfg: ZoneCfg = ZoneCfg(), include_invalidated: bool = False) -> list[RawZone]:
    n = len(c)
    if not swings or n == 0:
        return []
    a = float(atr[-1]) if atr[-1] > 0 else float(c[-1]) * 1e-6
    boxes = []
    for cl in _cluster(swings, cfg.cluster_atr * a, cfg.span_atr * a):
        lo, hi = min(s.price for s in cl) - cfg.pad_atr * a, max(s.price for s in cl) + cfg.pad_atr * a
        if hi - lo < cfg.min_width_atr * a:
            mid = (hi + lo) / 2
            lo, hi = mid - cfg.min_width_atr * a / 2, mid + cfg.min_width_atr * a / 2
        boxes.append((lo, hi, cl))
    zones: list[RawZone] = []
    buf = cfg.break_buffer_atr * a
    for lo, hi, cl in _merge_overlaps(boxes):
        first = min(cl, key=lambda s: s.idx)
        origin = "resistance" if sum(s.kind == "high" for s in cl) * 2 > len(cl) else "support" \
            if sum(s.kind == "low" for s in cl) * 2 > len(cl) else ("resistance" if cl[-1].kind == "high" else "support")
        role, flips, flip_idx = origin, 0, None
        side = lambda j: 1 if c[j] > hi else -1 if c[j] < lo else 0  # noqa: E731
        touches, last_touch_end, last_inter, in_run, entry = 0, -10**9, first.idx, False, 0
        for j in range(first.idx, n):
            if h[j] >= lo and l[j] <= hi:               # candle overlaps the zone
                last_inter = j
                if not in_run:
                    in_run, entry = True, (side(j - 1) if j > 0 else 0)
            elif in_run:                                # excursion ended: rejection if price left on the entry side
                in_run = False
                if entry != 0 and side(j) == entry and j - last_touch_end >= TOUCH_GAP:
                    touches, last_touch_end = touches + 1, j
            if j > first.idx:
                if role == "resistance" and c[j] > hi + buf:
                    role, flips, flip_idx = "support", flips + 1, j
                elif role == "support" and c[j] < lo - buf:
                    role, flips, flip_idx = "resistance", flips + 1, j
        touches = max(touches, 0)
        if touches < cfg.min_touches and flips == 0:
            continue
        status = "invalidated" if flips >= 2 else "active"
        if status == "invalidated" and not include_invalidated:
            continue
        avg_strength = sum(s.strength for s in cl) / len(cl)
        recency = max(0.0, 1.0 - (n - 1 - last_inter) / max(n, 1))
        strength = min(40.0, min(touches, 6) / 6 * 40) + avg_strength / 100 * 20 + 20 * recency \
            + (10 if flips == 1 else 0) - (10 if hi - lo > 1.5 * a else 0)
        zid = "z-" + hashlib.sha1(f"{symbol}|{tf}|{first.time}|{origin}".encode()).hexdigest()[:10]
        zones.append(RawZone(zid, role, origin, float(lo), float(hi), round(max(0.0, min(100.0, strength)), 1), touches,
                             first.time, first.confirmed_at, int(times[last_inter]), status, flips == 1,
                             int(times[flip_idx]) if flip_idx is not None and flips == 1 else None))
    return _select(zones, float(c[-1]), cfg.max_zones)


def _select(zones: list[RawZone], price: float, cap: int) -> list[RawZone]:
    """Top zones by strength, always keeping the nearest zone on each side of price."""
    keep = {z.id: z for z in sorted(zones, key=lambda z: -z.strength)[:cap]}
    for side in (lambda z: z.upper < price, lambda z: z.lower > price):
        cands = [z for z in zones if side(z)]
        if cands:
            near = min(cands, key=lambda z: abs((z.upper + z.lower) / 2 - price))
            keep[near.id] = near
    return sorted(keep.values(), key=lambda z: z.lower)


def nearest(zones: list[RawZone], price: float) -> tuple[str | None, str | None]:
    """(nearest support id below price, nearest resistance id above price) among active zones."""
    act = [z for z in zones if z.status == "active"]
    below = [z for z in act if z.upper <= price]
    above = [z for z in act if z.lower >= price]
    sup = max(below, key=lambda z: z.upper).id if below else None
    res = min(above, key=lambda z: z.lower).id if above else None
    return sup, res
