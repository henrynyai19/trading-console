import numpy as np
import pytest

from app.analysis.swing_detector import detect_swings
from app.providers.base import Candle, TF_SECONDS
from tests.analysis_helpers import zigzag

LEGS = [110, 100, 112, 102, 114, 104]
STEP = TF_SECONDS["15m"]


def swings_of(candles, **kw):
    return detect_swings(candles, STEP, **kw)


def test_detects_highs_and_lows_with_classification():
    c = zigzag(LEGS)
    s = swings_of(c)
    got = [(x.kind, x.idx, round(x.price, 1), x.label) for x in s]
    assert got == [("high", 7, 110.2, None), ("low", 15, 99.8, None), ("high", 23, 112.2, "HH"),
                   ("low", 31, 101.8, "HL"), ("high", 39, 114.2, "HH")]
    assert all(0 < x.strength <= 100 for x in s)


def test_lower_highs_and_lower_lows_are_labelled():
    s = swings_of(zigzag([114, 104, 112, 102, 110, 100, 108, 98]))
    assert [x.label for x in s if x.kind == "high"] == [None, "LH", "LH", "LH"]
    assert [x.label for x in s if x.kind == "low"] == [None, "LL", "LL"]


def test_not_confirmed_before_right_window_closes():
    c = zigzag(LEGS)
    right = 3
    assert not any(x.idx == 7 for x in swings_of(c[: 7 + right]))        # candles 0..9: pivot at 7 needs candle 10
    s = [x for x in swings_of(c[: 7 + right + 1]) if x.idx == 7]
    assert len(s) == 1 and s[0].confirmed_idx == 10
    assert s[0].confirmed_at == c[10].time + STEP                      # knowable when candle 10 CLOSES


def test_confirmed_swings_never_repaint_when_candles_are_replayed():
    c = zigzag(LEGS + [116, 106, 118, 108])
    full = swings_of(c, min_strength=10)
    for k in range(8, len(c) + 1):
        prefix = swings_of(c[:k], min_strength=10)
        assert prefix == [x for x in full if x.confirmed_idx <= k - 1], k


def test_flat_top_yields_exactly_one_pivot():
    base = zigzag([110, 100])
    t = base[-1].time
    flat = [Candle(t + STEP * (i + 1), 100 + i, 111, 99, 100 + i, 1, True) for i in range(2)]  # two equal highs
    tail = [Candle(t + STEP * (i + 3), 100 - i, 105, 90, 100 - i, 1, True) for i in range(5)]
    highs = [x for x in swings_of(base + flat + tail) if x.kind == "high" and x.price == 111]
    assert len(highs) == 1


def test_minor_swings_can_be_filtered():
    c = zigzag(LEGS)
    all_s = swings_of(c)
    kept = swings_of(c, min_strength=max(x.strength for x in all_s) + 1)
    assert all_s and kept == []


def test_short_input_and_bad_window():
    assert swings_of(zigzag([110], bars=4)) == []
    with pytest.raises(ValueError):
        swings_of(zigzag(LEGS), left=0)


def test_works_across_price_scales():
    shapes = []
    for scale in (1.0, 1000.0, 0.011):
        s = swings_of(zigzag(LEGS, scale=scale))
        shapes.append([(x.kind, x.idx, x.label, round(x.strength, 1)) for x in s])
    assert shapes[0] == shapes[1] == shapes[2]
