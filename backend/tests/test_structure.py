import numpy as np

from app.analysis.indicators import atr
from app.analysis.structure_analyzer import analyze_structure
from app.analysis.swing_detector import arrays, detect_swings
from app.providers.base import TF_SECONDS
from tests.analysis_helpers import developing, zigzag

STEP = TF_SECONDS["15m"]
# up-trend with higher highs/lows, then a break below the protected low (CHoCH), then continuation lower (BOS)
LEGS = [110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 119, 132, 110, 118, 100, 108, 94, 104, 88]


def run(candles, **kw):
    t, _, h, l, c = arrays(candles)
    a = atr(h, l, c)
    sw = detect_swings(candles, STEP, 3, 3, 10, a)
    return analyze_structure(t, c, a, sw, STEP, **kw), sw


def test_bos_continuation_then_choch_reversal_then_bos():
    st, _ = run(zigzag(LEGS))
    kinds = [(e.kind, e.direction) for e in st.events]
    assert ("BOS", "bullish") in kinds and ("CHoCH", "bearish") in kinds
    first_choch = kinds.index(("CHoCH", "bearish"))
    assert all(k == ("BOS", "bullish") for k in kinds[:first_choch])           # only continuation before the reversal
    assert ("BOS", "bearish") in kinds[first_choch + 1:]                      # continuation in the new direction
    assert st.state == "bear"


def test_every_swing_level_is_broken_at_most_once_and_one_event_per_candle():
    st, _ = run(zigzag(LEGS))
    keys = [(e.direction == "bullish", e.broken_idx) for e in st.events]
    assert len(keys) == len(set(keys))
    assert len({e.break_idx for e in st.events}) == len(st.events)


def test_event_timestamps_are_when_it_became_knowable():
    c = zigzag(LEGS)
    st, _ = run(c)
    for e in st.events:
        assert e.broken_time < e.swing_confirmed_at <= e.break_time          # swing was confirmed before it could break
        assert e.confirmed_at == e.break_time + STEP                         # known at the close of the break candle
        assert c[e.break_idx].close != 0 and (c[e.break_idx].close > e.level if e.direction == "bullish" else c[e.break_idx].close < e.level)


def test_replay_events_appear_only_when_data_allows():
    c = zigzag(LEGS)
    full, _ = run(c)
    for k in range(20, len(c) + 1):
        part, _ = run(c[:k])
        assert part.events == [e for e in full.events if e.break_idx <= k - 1], k


def test_a_wick_through_a_level_is_not_a_break_but_a_close_is():
    from app.providers.base import Candle
    c = zigzag(LEGS)
    full, _ = run(c)
    e = next(x for x in full.events if x.kind == "BOS" and x.direction == "bullish")
    before = [x for x in full.events if x.break_idx < e.break_idx]
    k = e.break_idx
    wick = Candle(c[k].time, c[k - 1].close, e.level + 5, c[k - 1].close - 0.2, e.level - 0.5, 1, True)  # pierces, closes below
    st, _ = run(c[:k] + [wick])
    assert st.events == before                       # a wick is not a structural break
    close_above = Candle(c[k].time, c[k - 1].close, e.level + 5, c[k - 1].close - 0.2, e.level + 0.5, 1, True)
    st2, _ = run(c[:k] + [close_above])
    assert len(st2.events) == len(before) + 1 and st2.events[-1].break_idx == k   # the same candle closing above is one


def test_no_events_without_established_structure():
    st, _ = run(zigzag([101, 100.5, 101.2], bars=6))
    assert st.events == [] and st.state is None


def test_range_market_does_not_spam_events():
    st, _ = run(zigzag([110, 100] * 10))
    assert len(st.events) == 0
