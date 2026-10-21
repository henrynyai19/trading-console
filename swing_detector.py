"""Pivot swing detection with explicit confirmation timing.

A pivot high at candle i needs `right` later candles that do not exceed it, so it is only *known* when candle i+right
closes (`confirmed_idx`). Left side is strict (>), right side non-strict (>=): a flat top yields exactly one pivot, the
first. Given the same prefix of candles, earlier swings never change, so confirmed swings cannot repaint.
"""
from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..providers.base import Candle
from .indicators import atr as atr_fn


@dataclass(frozen=True)
class Swing:
    idx: int
    kind: str  # "high" | "low"
    price: float
    time: int
    confirmed_idx: int
    confirmed_at: int
    strength: float  # 0-100 prominence score, in ATRs. A quality score, not a probability.
    label: str | None = None


def arrays(candles: Sequence[Candle]):
    return (np.array([c.time for c in candles], dtype=np.int64), np.array([c.open for c in candles], dtype=float),
            np.array([c.high for c in candles], dtype=float), np.array([c.low for c in candles], dtype=float),
            np.array([c.close for c in candles], dtype=float))


def detect_swings(candles: Sequence[Candle], step: int, left: int = 3, right: int = 3, min_strength: float = 0.0,
                  atr_arr: np.ndarray | None = None) -> list[Swing]:
    n = len(candles)
    if left < 1 or right < 1:
        raise ValueError("left and right windows must be >= 1")
    if n < left + right + 1:
        return []
    t, _, h, l, c = arrays(candles)
    a = atr_fn(h, l, c) if atr_arr is None else atr_arr
    lmax = sliding_window_view(h, left).max(axis=1)      # window k covers k..k+left-1
    lmin = sliding_window_view(l, left).min(axis=1)
    rmax = sliding_window_view(h, right).max(axis=1)
    rmin = sliding_window_view(l, right).min(axis=1)
    out: list[Swing] = []
    for i in range(left, n - right):
        for kind in ("high", "low"):
            if kind == "high":
                if not (h[i] > lmax[i - left] and h[i] >= rmax[i + 1]):
                    continue
                prom = h[i] - max(lmin[i - left], rmin[i + 1])
                price = h[i]
            else:
                if not (l[i] < lmin[i - left] and l[i] <= rmin[i + 1]):
                    continue
                prom = min(lmax[i - left], rmax[i + 1]) - l[i]
                price = l[i]
            strength = 0.0 if a[i] <= 0 else float(min(100.0, 100.0 * prom / (4.0 * a[i])))
            if strength < min_strength:
                continue
            ci = i + right
            out.append(Swing(i, kind, float(price), int(t[i]), ci, int(t[ci]) + step, round(strength, 1)))
    return classify(out, a)


def classify(swings: list[Swing], atr: np.ndarray | None = None, eq_atr: float = 0.1) -> list[Swing]:
    """Label each swing against the previous swing of the same kind: HH/LH/EQH and HL/LL/EQL.
    Differences within `eq_atr` x ATR (ATR at the swing candle: known data only) count as equal."""
    last_h = last_l = None
    out = []
    for s in sorted(swings, key=lambda s: (s.idx, s.kind)):
        tol = 0.0 if atr is None else eq_atr * float(atr[s.idx])
        if s.kind == "high":
            label = None if last_h is None else ("EQH" if abs(s.price - last_h) <= tol else "HH" if s.price > last_h else "LH")
            last_h = s.price
        else:
            label = None if last_l is None else ("EQL" if abs(s.price - last_l) <= tol else "HL" if s.price > last_l else "LL")
            last_l = s.price
        out.append(replace(s, label=label))
    return out
