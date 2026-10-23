import json

import numpy as np
import pytest

from app.analysis.analysis_service import AnalysisConfig, analyze_candles, closed_candles
from app.analysis.indicators import atr
from app.analysis.multi_timeframe import HIERARCHY, combine, higher_timeframes, tf_trend
from app.analysis.structure_analyzer import StructureResult
from app.analysis.support_resistance import ZoneCfg, build_zones, nearest
from app.analysis.swing_detector import Swing, arrays, detect_swings
from app.analysis.trend_detector import detect_trend
from app.analysis.trendline_detector import detect_trendlines
from app.providers.base import TF_SECONDS, TIMEFRAMES, Candle
from tests.analysis_helpers import BEAR, BULL, NOW, RANGE, developing, zigzag

STEP = TF_SECONDS["15m"]
CFG = AnalysisConfig()


def pipeline(candles, **zkw):
    t, _, h, l, c = arrays(candles)
    a = atr(h, l, c)
    sw = detect_swings(candles, STEP, 3, 3, 10, a)
    return t, h, l, c, a, sw, build_zones(t, h, l, c, a, sw, "XAUUSD", "15m", STEP, **zkw)


# ---------------- support / resistance ----------------
def test_range_produces_one_support_and_one_resistance_zone():
    t, h, l, c, a, sw, z = pipeline(zigzag(RANGE))
    res = [x for x in z if x.type == "resistance"]
    sup = [x for x in z if x.type == "support"]
    assert len(res) == 1 and len(sup) == 1                      # clustered, not one zone per swing
    assert res[0].lower <= 110.2 <= res[0].upper and sup[0].lower <= 99.8 <= sup[0].upper
    assert res[0].touches >= 2 and sup[0].touches >= 2
    assert all(0 <= x.strength <= 100 and x.status == "active" for x in z)
    assert z[0].created_at > z[0].start_time                    # known only after the swing was confirmed


def test_zones_do_not_overlap_and_nearest_are_on_the_correct_side():
    t, h, l, c, a, sw, z = pipeline(zigzag(RANGE + [105]))
    for x, y in zip(z, z[1:]):
        assert x.upper < y.lower
    s, r = nearest(z, float(c[-1]))
    by = {x.id: x for x in z}
    assert by[s].upper <= c[-1] <= by[r].lower


def test_role_reversal_and_invalidation():
    up = zigzag(RANGE + [128, 113, 130])                             # breaks resistance (~110.2), retests above it
    _, _, _, _, _, _, z = pipeline(up)
    flipped = [x for x in z if x.role_reversed]
    assert flipped and flipped[0].type == "support" and flipped[0].origin_type == "resistance"
    assert flipped[0].role_reversal_time is not None
    down = zigzag(RANGE + [128, 113, 130, 90])                       # then closes back through it: accepted both sides
    _, _, _, _, _, _, z2 = pipeline(down)
    assert not any(x.origin_type == "resistance" and x.lower <= 110.2 <= x.upper for x in z2)
    _, _, _, _, _, _, z3 = pipeline(down, include_invalidated=True)
    inv = [x for x in z3 if x.status == "invalidated"]
    assert inv and inv[0].lower <= 110.2 <= inv[0].upper


def test_zones_are_scale_invariant():
    shapes = []
    for scale in (1.0, 1000.0, 0.011):
        z = pipeline(zigzag(RANGE, scale=scale))[-1]
        shapes.append([(x.type, x.touches, round(x.strength, 1)) for x in z])
    assert shapes[0] == shapes[1] == shapes[2]


def test_max_zones_cap():
    zigs = [100, 110, 101, 120, 102, 130, 103, 140, 104, 150, 105, 160]
    z = pipeline(zigzag(zigs * 2), cfg=ZoneCfg(max_zones=2, min_touches=0))[-1]
    assert len(z) <= 4  # cap plus the guaranteed nearest zone on each side


# ---------------- trend ----------------
def classify(legs, bars=10, **kw):
    core = analyze_candles("XAUUSD", "15m", zigzag(legs, bars=bars, **kw), NOW, CFG)
    return core.body.trend


def test_strong_bull_and_bear_trends_with_transparent_components():
    bull = classify(BULL * 1 + [144, 135, 148, 139, 152, 143, 156, 147, 160, 151, 164, 155, 168])
    assert bull["classification"] in ("Strong Bullish", "Bullish") and bull["score"] > 40
    assert {c["name"] for c in bull["components"]} == {"Swing structure", "Structural event", "Moving averages", "Persistence", "Momentum"}
    assert any("not a probability" in x for x in bull["explanation"])
    bear = classify([200 - x for x in BULL] + [200 - y for y in [144, 135, 148, 139, 152, 143, 156, 147, 160, 151, 164, 155, 168]], start=100)
    assert bear["classification"] in ("Strong Bearish", "Bearish") and bear["score"] < -40


def test_sideways_range():
    t = classify(RANGE + RANGE, bars=10)
    assert t["classification"] == "Sideways" and t["components"][0]["points"] == 0
    assert any("range-bound" in x for x in t["explanation"])  # averages alone cannot make a range directional


def test_recent_choch_makes_trend_transitional_not_bearish():
    legs = BULL + [100]                                   # one deep leg below the protected low, not yet confirmed as a swing
    t = classify(legs, bars=10)
    assert t["classification"] == "Transitional"


def test_moving_averages_cannot_override_structure():
    n = 300
    closes = np.linspace(200.0, 100.0, n)               # strongly bearish averages and momentum
    a = np.full(n, 1.0)
    mk = lambda kind, idx, label: Swing(idx, kind, 100.0 + idx, idx, idx + 3, idx + 4, 50.0, label)  # noqa: E731
    swings = [mk("high", 10, None), mk("low", 20, None), mk("high", 30, "HH"), mk("low", 40, "HL")]
    t = detect_trend(closes, a, swings, StructureResult(state="bear", last_event_idx=0))
    assert t["structure_state"] == "bearish" and t["components"][0]["points"] > 0
    assert t["classification"] == "Transitional" and any("not overridden" in x for x in t["explanation"])


def test_insufficient_structure_gives_no_trend():
    core = analyze_candles("XAUUSD", "15m", zigzag([101, 102, 103], bars=25), NOW, CFG)
    assert core.status == "ok" and core.body.trend is None


# ---------------- trendlines ----------------
RISING = [110, 100, 114, 104, 118, 108, 122, 112, 126, 116]


def test_bullish_trendline_through_rising_lows_then_break():
    t, h, l, c, a, sw, _ = pipeline(zigzag(RISING))
    lines = detect_trendlines(t, c, a, sw, "15m")
    bull = [x for x in lines if x.direction == "bullish"]
    assert bull and bull[0].touches >= 3 and bull[0].status == "active" and bull[0].break_time is None
    assert bull[0].end_time > bull[0].start_time and bull[0].slope > 0
    broken = zigzag(RISING + [95])                       # closes well below the line
    t, h, l, c, a, sw, _ = pipeline(broken)
    b = [x for x in detect_trendlines(t, c, a, sw, "15m") if x.direction == "bullish"]
    assert b and b[0].status == "broken" and b[0].break_time is not None


def test_bearish_trendline_through_falling_highs():
    t, h, l, c, a, sw, _ = pipeline(zigzag([200 - x for x in RISING], start=100))
    assert any(x.direction == "bearish" and x.touches >= 3 for x in detect_trendlines(t, c, a, sw, "15m"))


def test_no_trendline_when_price_cuts_through_between_anchors():
    t, h, l, c, a, sw, _ = pipeline(zigzag(RANGE))
    assert detect_trendlines(t, c, a, sw, "15m") == []   # flat lows: not a rising line


# ---------------- multi-timeframe ----------------
def T(tf, cls):
    return tf_trend(tf, None if cls is None else dict(classification=cls, score=50.0, structure_state="bullish" if "Bull" in cls else "bearish" if "Bear" in cls else "undetermined"))


def test_hierarchy_covers_all_timeframes_and_points_upward():
    assert set(HIERARCHY) == set(TIMEFRAMES)
    for tf, hs in HIERARCHY.items():
        assert all(TF_SECONDS[h] > TF_SECONDS[tf] for h in hs)
    assert higher_timeframes("5m") == ["15m", "1h"] and higher_timeframes("15m") == ["1h", "4h"]
    assert higher_timeframes("1h") == ["4h", "1d"] and higher_timeframes("4h") == ["1d", "1w"]


def test_alignment_rules_never_force_agreement():
    r = combine(T("15m", "Bullish"), [T("1h", "Strong Bullish"), T("4h", "Bullish")])
    assert (r["alignment"], r["bias"]) == ("aligned", "Bullish")
    r = combine(T("15m", "Bullish"), [T("1h", "Bullish"), T("4h", "Bearish")])
    assert (r["alignment"], r["bias"]) == ("conflicting", "Mixed") and any("vs" in x for x in r["conflicts"])
    r = combine(T("15m", "Bullish"), [T("1h", "Transitional"), T("4h", "Bullish")])
    assert (r["alignment"], r["bias"]) == ("partial", "Leaning Bullish")
    r = combine(T("15m", "Bearish"), [T("1h", None), T("4h", "Bearish")])
    assert (r["alignment"], r["bias"]) == ("partial", "Leaning Bearish")      # an unavailable timeframe blocks 'aligned'
    assert combine(T("15m", "Sideways"), [T("1h", "Sideways")])["bias"] == "Neutral"
    assert combine(T("15m", None), [T("1h", None)])["bias"] == "Unavailable"
    assert combine(T("1w", "Bullish"), [])["alignment"] == "partial"


# ---------------- data hygiene / robustness ----------------
def test_developing_and_provisional_candles_never_change_analysis():
    c = zigzag(BULL)
    base = analyze_candles("XAUUSD", "15m", c, NOW, CFG).body
    spike = developing(c[-1], close=c[-1].close * 3)
    prov = Candle(spike.time + STEP, 1, 999, 1, 999, 0, True, True)
    withdev = analyze_candles("XAUUSD", "15m", c + [spike, prov], NOW, CFG).body
    assert [e.id for e in withdev.events] == [e.id for e in base.events]
    assert [s.id for s in withdev.swings] == [s.id for s in base.swings]
    assert len(closed_candles(c + [spike, prov])) == len(c)


def test_missing_candles_and_extreme_volatility_stay_finite():
    c = zigzag(BULL)
    gap = c[:60] + c[64:]
    core = analyze_candles("XAUUSD", "15m", gap, NOW, CFG)
    assert any("missing-candle" in w for w in core.body.warnings)
    c2 = zigzag(BULL)
    s = c2[80]
    c2[80] = Candle(s.time, s.open, s.high * 4, s.low * 0.25, s.close, 1, True)
    body = analyze_candles("XAUUSD", "15m", c2, NOW, CFG).body
    assert any("Extreme" in w for w in body.warnings)
    json.dumps([x.model_dump() for x in body.swings + body.zones + body.events + body.trendlines], allow_nan=False)


def test_bad_candles_are_dropped():
    c = zigzag(BULL)
    bad = [Candle(c[-1].time + STEP, float("nan"), 1, 1, 1, 0, True), Candle(c[-1].time + 2 * STEP, 5, 1, 4, 3, 0, True)]
    assert len(closed_candles(c + bad)) == len(c)


def test_insufficient_and_stale_data_withhold_analysis():
    few = analyze_candles("XAUUSD", "15m", zigzag(BULL)[:30], NOW, CFG)
    assert few.status == "insufficient_data" and few.body is None
    c = zigzag(BULL)
    stale = analyze_candles("XAUUSD", "15m", c, NOW + 86400, CFG)          # a trading day later, no new candle
    assert stale.status == "stale" and stale.body is None and stale.freshness.status == "stale"
