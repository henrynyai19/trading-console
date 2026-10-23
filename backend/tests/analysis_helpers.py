"""Synthetic candle builders. SYNTHETIC DATA IS CONFINED TO TESTS."""
from app.providers.base import TF_SECONDS, Candle

NOW = 1_700_086_440  # a Wednesday 22:14 UTC, outside the weekend closure


def zigzag(legs, bars=8, start=100.0, wick=0.2, tf="15m", end=None, scale=1.0, first=None) -> list[Candle]:
    """Closed candles that move linearly through each target price in `legs` (one leg = `bars` candles)."""
    step = TF_SECONDS[tf]
    n = len(legs) * bars
    base = (end if end is not None else NOW) - n * step  # last candle OPENS one step before `end`
    out, prev = [], start
    for li, target in enumerate(legs):
        leg_start = prev
        for k in range(1, bars + 1):
            c = leg_start + (target - leg_start) * k / bars
            o = prev
            idx = li * bars + k - 1
            out.append(Candle(base + idx * step, o * scale, (max(o, c) + wick) * scale, (min(o, c) - wick) * scale,
                              c * scale, 10, True))
            prev = c
    return out


def developing(last: Candle, tf="15m", close=None) -> Candle:
    close = last.close if close is None else close
    step = TF_SECONDS[tf]
    return Candle(last.time + step, last.close, max(last.close, close) + 0.1, min(last.close, close) - 0.1, close, 1, False)


BULL = [110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 119, 132, 123, 136, 127, 140]
BEAR = [200 - x for x in BULL]
RANGE = [110, 100] * 8
