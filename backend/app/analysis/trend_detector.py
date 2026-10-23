"""Transparent trend score in [-100, 100] with named components. A rule-based quality score, not a probability.

Components (max points): swing structure 40, latest structural event 15, moving averages 25, persistence 10, momentum 15
(total capped at +-100).
Moving averages and momentum can never override structure: if the structure sign and the total score disagree, the
classification is Transitional.
"""
import numpy as np

from .indicators import ema
from .structure_analyzer import StructureResult
from .swing_detector import Swing

EVENT_WINDOW = 100
RECENT_CHOCH = 25


def _sign(x: float, dead: float = 0.0) -> int:
    return 1 if x > dead else -1 if x < -dead else 0


def detect_trend(closes: np.ndarray, atr: np.ndarray, swings: list[Swing], st: StructureResult) -> dict | None:
    n = len(closes)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None  # not enough confirmed structure to classify
    comps: list[dict] = []

    # 1. swing structure: last two labelled highs and lows
    lab = [s.label for s in highs[-2:]] + [s.label for s in lows[-2:]]
    pts = sum(10 if x in ("HH", "HL") else -10 if x in ("LH", "LL") else 0 for x in lab)
    comps.append(dict(name="Swing structure", points=pts, max_points=40,
                      detail=f"latest highs {[s.label for s in highs[-2:]]}, lows {[s.label for s in lows[-2:]]}"))
    struct_sign = _sign(pts, 15)

    # 2. latest structural event
    ev = st.events[-1] if st.events else None
    ev_pts, ev_detail = 0.0, "no structural event yet"
    if ev is not None and n - 1 - ev.break_idx <= EVENT_WINDOW:
        ev_pts = 15.0 if ev.direction == "bullish" else -15.0
        ev_detail = f"latest {ev.direction} {ev.kind}, {n - 1 - ev.break_idx} bars ago"
    elif ev is not None:
        ev_detail = f"latest event is older than {EVENT_WINDOW} bars"
    comps.append(dict(name="Structural event", points=ev_pts, max_points=15, detail=ev_detail))

    # 3. moving averages (support only)
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    ma_pts, parts = 0.0, []
    if not np.isnan(e50[-1]):
        ma_pts += 8 * _sign(closes[-1] - e50[-1]); parts.append("price vs EMA50")
        if not np.isnan(e20[-1]):
            ma_pts += 8 * _sign(e20[-1] - e50[-1]); parts.append("EMA20 vs EMA50")
    if not np.isnan(e200[-1]) and not np.isnan(e50[-1]):
        ma_pts += 9 * _sign(e50[-1] - e200[-1]); parts.append("EMA50 vs EMA200")
    comps.append(dict(name="Moving averages", points=ma_pts, max_points=25,
                      detail=(", ".join(parts) if parts else "not enough candles for EMAs")
                      + ("" if not np.isnan(e200[-1]) else " (EMA200 unavailable)")))

    # 4. persistence of the structural trend state
    pers = 0.0
    if st.state is not None and st.last_event_idx is not None:
        pers = (10 if st.state == "bull" else -10) * min((n - 1 - st.last_event_idx) / 30, 1.0)
    comps.append(dict(name="Persistence", points=round(pers, 1), max_points=10,
                      detail=f"structure state {st.state or 'undetermined'}"))

    # 5. momentum: EMA20 slope over 10 candles in ATRs
    mom = 0.0
    if n > 10 and not np.isnan(e20[-1]) and not np.isnan(e20[-11]) and atr[-1] > 0:
        mom = float(np.clip((e20[-1] - e20[-11]) / atr[-1], -1, 1)) * 15
    comps.append(dict(name="Momentum", points=round(mom, 1), max_points=15, detail="EMA20 slope over 10 candles / ATR"))

    score = round(float(np.clip(sum(c["points"] for c in comps), -100, 100)), 1)
    for c in comps:
        c["points"] = round(float(c["points"]), 1)
    sc = _sign(score)
    recent_choch = ev is not None and ev.kind == "CHoCH" and n - 1 - ev.break_idx <= RECENT_CHOCH
    ev_sign = 1 if ev is not None and ev.direction == "bullish" else -1

    why = []
    labs = [x for x in lab if x]
    flat = bool(labs) and all(x in ("EQH", "EQL") for x in labs) and not recent_choch
    if flat and (ev is None or n - 1 - ev.break_idx > RECENT_CHOCH):
        cls = "Sideways"
        why.append("swing highs and lows are flat (range-bound); averages cannot make a range directional")
    elif struct_sign == 0:
        cls = "Sideways" if abs(score) < 20 else "Transitional"
        why.append("swing structure is mixed" + (", with directional averages/momentum" if cls == "Transitional" else ""))
    elif sc != 0 and sc != struct_sign:
        cls = "Transitional"
        why.append("moving averages/momentum disagree with swing structure, and structure is not overridden")
    elif recent_choch and ev_sign != struct_sign:
        cls = "Transitional"
        why.append(f"a recent {ev.direction} CHoCH has not yet been confirmed by the swing sequence")
    elif abs(score) < 20:
        cls = "Sideways"
        why.append("score is within the neutral band (-20..20)")
    else:
        strong = abs(score) >= 60 and abs(pts) >= 30
        cls = ("Strong " if strong else "") + ("Bullish" if sc > 0 else "Bearish")
        why.append("structure, events and averages agree" + (" strongly" if strong else ""))

    state = {"bull": "bullish", "bear": "bearish", None: "undetermined"}[st.state]
    bars = None if st.last_event_idx is None else n - 1 - st.last_event_idx
    explanation = [f"{c['name']}: {c['points']:+g}/{c['max_points']:g} ({c['detail']})" for c in comps]
    explanation.append(f"Classified {cls}: {why[0]}. Score is a rule-based quality measure, not a probability.")
    return dict(classification=cls, score=score, components=comps, explanation=explanation, structure_state=state,
                bars_since_last_event=bars)
