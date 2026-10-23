import logging
from datetime import datetime, timezone
from typing import Iterable

from .providers.base import TF_SECONDS, Candle

log = logging.getLogger("candles")


def rank(c: Candle) -> int:
    """official closed (2) > official developing (1) > provisional (0)."""
    if c.provisional:
        return 0
    return 2 if c.complete else 1


def merge_candles(store: dict[int, Candle], incoming: Iterable[Candle]) -> list[Candle]:
    """Merge by candle time. Lower-ranked rows cannot replace higher-ranked rows; closed-row corrections are logged."""
    changed: list[Candle] = []
    for c in incoming:
        cur = store.get(c.time)
        if cur is None or (rank(c) >= rank(cur) and c != cur):
            if cur is not None and cur.complete and not cur.provisional and c.complete and not c.provisional and c != cur:
                log.warning("provider corrected a completed candle at %s", c.time)
            store[c.time] = c
            changed.append(c)
    return changed


def _weekend(ts: int) -> bool:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    wd = d.weekday()  # Mon=0
    return wd == 5 or (wd == 4 and d.hour >= 21) or (wd == 6 and d.hour < 22)


def find_gaps(times: list[int], tf: str) -> list[tuple[int, int]]:
    """Gaps (first_missing, last_missing) between consecutive candles, ignoring the weekend close."""
    step = TF_SECONDS[tf]
    gaps = []
    for a, b in zip(times, times[1:]):
        if b - a <= step:
            continue
        missing = [s for s in range(a + step, b, step) if not _weekend(s)]
        if missing:
            gaps.append((missing[0], missing[-1]))
    return gaps


def is_fresh(last_cached: int, tf: str, now: float, max_candles: int = 500) -> bool:
    """Fresh enough that a small tail fetch (<= max_candles) completes the cache."""
    return 0 <= now - last_cached <= TF_SECONDS[tf] * max_candles


_WEEK = 604800
_CLOSE_ANCHOR = 1700254800  # a Friday 21:00 UTC; the FX weekend closure lasts 49 h from there (until Sunday 22:00 UTC)
_CLOSURE = 49 * 3600


def in_weekend_closure(ts: float) -> bool:
    return 0 <= (ts - _CLOSE_ANCHOR) % _WEEK < _CLOSURE


def weekend_overlap(a: float, b: float) -> float:
    """Seconds of [a, b] that fall inside the weekend closure."""
    if b <= a:
        return 0.0
    total, k = 0.0, int((a - _CLOSE_ANCHOR) // _WEEK) - 1
    while True:
        s = _CLOSE_ANCHOR + k * _WEEK
        if s > b:
            return total
        total += max(0.0, min(b, s + _CLOSURE) - max(a, s))
        k += 1
