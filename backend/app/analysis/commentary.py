"""Deterministic commentary built only from computed facts. Missing facts produce no sentence."""
from ..providers.base import INSTRUMENTS
from .multi_timeframe import LABEL
from .models import AnalysisResponse


def build_commentary(r: AnalysisResponse) -> list[str]:
    if r.status != "ok" or r.trend is None:
        return []
    dp = INSTRUMENTS[r.instrument]["digits"]
    fmt = lambda x: f"{x:.{dp}f}"  # noqa: E731
    tf = LABEL[r.timeframe]
    out = []
    s = f"{tf} structure is {r.trend.classification.lower()} (trend score {r.trend.score:+.0f}/100, a rule-based score and not a probability)"
    hs = [x.label for x in r.swings if x.kind == "high" and x.label][-2:]
    ls = [x.label for x in r.swings if x.kind == "low" and x.label][-2:]
    if hs and ls:
        s += f", with the latest confirmed swing highs {'/'.join(hs)} and lows {'/'.join(ls)}"
    out.append(s + ".")
    if r.events:
        e = r.events[-1]
        out.append(f"The most recent structural event is a {e.direction} {e.kind}, a close beyond {fmt(e.level)}.")
    zs = {z.id: z for z in r.zones}
    parts = []
    if r.nearest_support:
        z = zs[r.nearest_support]; parts.append(f"nearest support {fmt(z.lower)}-{fmt(z.upper)} (strength {z.strength:.0f})")
    if r.nearest_resistance:
        z = zs[r.nearest_resistance]; parts.append(f"nearest resistance {fmt(z.lower)}-{fmt(z.upper)} (strength {z.strength:.0f})")
    if parts:
        out.append("; ".join(parts).capitalize() + ".")
    act = [t for t in r.trendlines if t.status == "active"]
    if act:
        out.append(f"{len(act)} active trendline(s): " + ", ".join(f"{t.direction} ({t.touches} touches)" for t in act) + ".")
    if r.mtf and r.mtf.higher:
        out.append("Higher timeframes: " + r.mtf.summary)
    if r.warnings:
        out.append("Data caveat: " + r.warnings[0])
    return out
