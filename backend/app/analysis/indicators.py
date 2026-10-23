"""Pure numeric helpers. Every value at index i depends only on candles 0..i (no lookahead)."""
import numpy as np


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    tr = h - l
    if len(c) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder ATR. Before `period` candles exist it is the running mean of the true range (never NaN)."""
    tr = true_range(h, l, c)
    out = np.empty_like(tr)
    run = 0.0
    for i, v in enumerate(tr):
        if i < period:
            run += v
            out[i] = run / (i + 1)
        else:
            out[i] = (out[i - 1] * (period - 1) + v) / period
    return out


def ema(x: np.ndarray, period: int) -> np.ndarray:
    """EMA seeded with the SMA of the first `period` values; NaN until then."""
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    out[period - 1] = x[:period].mean()
    k = 2.0 / (period + 1)
    for i in range(period, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out
