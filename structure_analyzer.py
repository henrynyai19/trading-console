"""BOS / CHoCH detection, processed candle by candle so nothing can use future data.

Rules (closed candles only):
* A swing becomes usable on the candle AFTER its confirmation candle.
* Seed: with no trend yet, the first close beyond the latest swing high (low) starts a bullish (bearish) trend. No event
  is emitted: there is no prior structure to call it a continuation or a reversal.
* Bullish trend: close above the latest unbroken swing high formed after the last event = bullish BOS.
  Close below the protected low = bearish CHoCH (trend flips). The protected low is the pullback low that supported the
  last bullish break, raised whenever a higher low is confirmed. Lower lows that never close below it do not count.
* Bearish trend mirrors this. Every swing level can be broken only once; at most one event per candle.
"""
from dataclasses import dataclass, field

import numpy as np

from .swing_detector import Swing


@dataclass(frozen=True)
class StructEvent:
    kind: str       # BOS | CHoCH
    direction: str  # bullish | bearish
    level: float
    broken_idx: int
    broken_time: int
    swing_confirmed_at: int
    break_idx: int
    break_time: int
    confirmed_at: int


@dataclass
class StructureResult:
    events: list[StructEvent] = field(default_factory=list)
    state: str | None = None  # "bull" | "bear" | None
    last_event_idx: int | None = None  # candle index of the last event or the seed
    protected_level: float | None = None


def analyze_structure(times: np.ndarray, closes: np.ndarray, atr: np.ndarray, swings: list[Swing], step: int,
                      buffer_atr: float = 0.0) -> StructureResult:
    ordered = sorted(swings, key=lambda s: (s.confirmed_idx, s.idx))
    ptr, highs, lows, consumed = 0, [], [], set()
    trend, prot_low, prot_high, last_ev = None, None, None, -1
    events: list[StructEvent] = []

    def emit(kind, direction, ref: Swing, j):
        events.append(StructEvent(kind, direction, ref.price, ref.idx, ref.time, ref.confirmed_at, j, int(times[j]),
                                  int(times[j]) + step))
        consumed.add((ref.kind, ref.idx))

    for j in range(len(closes)):
        while ptr < len(ordered) and ordered[ptr].confirmed_idx <= j - 1:
            s = ordered[ptr]
            ptr += 1
            if s.kind == "high":
                highs.append(s)
                if trend == "bear" and (prot_high is None or s.price < prot_high.price):
                    prot_high = s
            else:
                lows.append(s)
                if trend == "bull" and (prot_low is None or s.price > prot_low.price):
                    prot_low = s
        c, buf = closes[j], buffer_atr * atr[j]

        if trend is None:
            if highs and lows:
                if c > highs[-1].price + buf:
                    trend, prot_low, last_ev = "bull", lows[-1], j
                    consumed.add(("high", highs[-1].idx))
                elif c < lows[-1].price - buf:
                    trend, prot_high, last_ev = "bear", highs[-1], j
                    consumed.add(("low", lows[-1].idx))
            continue

        if trend == "bull":
            if prot_low is not None and c < prot_low.price - buf:
                emit("CHoCH", "bearish", prot_low, j)
                trend, prot_high, prot_low, last_ev = "bear", (highs[-1] if highs else None), None, j
                continue
            ref = next((s for s in reversed(highs) if s.idx > last_ev and ("high", s.idx) not in consumed), None)
            if ref is not None and c > ref.price + buf:
                emit("BOS", "bullish", ref, j)
                pull = [s for s in lows if s.idx > ref.idx]
                if pull:
                    prot_low = min(pull, key=lambda s: s.price)
                last_ev = j
        else:
            if prot_high is not None and c > prot_high.price + buf:
                emit("CHoCH", "bullish", prot_high, j)
                trend, prot_low, prot_high, last_ev = "bull", (lows[-1] if lows else None), None, j
                continue
            ref = next((s for s in reversed(lows) if s.idx > last_ev and ("low", s.idx) not in consumed), None)
            if ref is not None and c < ref.price - buf:
                emit("BOS", "bearish", ref, j)
                pull = [s for s in highs if s.idx > ref.idx]
                if pull:
                    prot_high = max(pull, key=lambda s: s.price)
                last_ev = j

    prot = prot_low if trend == "bull" else prot_high if trend == "bear" else None
    return StructureResult(events, trend, last_ev if last_ev >= 0 else None, prot.price if prot else None)
