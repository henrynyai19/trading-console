"""Configurable timeframe hierarchy and alignment. Disagreement is reported, never forced into a bias."""
HIERARCHY = {
    "1m": ["5m", "15m"], "5m": ["15m", "1h"], "15m": ["1h", "4h"], "30m": ["1h", "4h"],
    "1h": ["4h", "1d"], "4h": ["1d", "1w"], "1d": ["1w"], "1w": [],
}
LABEL = {"1m": "1M", "5m": "5M", "15m": "15M", "30m": "30M", "1h": "1H", "4h": "4H", "1d": "D", "1w": "W"}
_DIR = {"Strong Bullish": "bullish", "Bullish": "bullish", "Strong Bearish": "bearish", "Bearish": "bearish",
        "Sideways": "neutral", "Transitional": "neutral"}


def higher_timeframes(tf: str) -> list[str]:
    return list(HIERARCHY[tf])


def tf_trend(tf: str, trend: dict | None, reason: str | None = None) -> dict:
    if trend is None:
        return dict(timeframe=tf, classification=None, direction="unavailable", score=None,
                    structure_state="undetermined", reason=reason or "not enough confirmed structure")
    return dict(timeframe=tf, classification=trend["classification"], direction=_DIR[trend["classification"]],
                score=trend["score"], structure_state=trend["structure_state"], reason=None)


def combine(primary: dict, higher: list[dict]) -> dict:
    everyone = [primary] + higher
    avail = [t for t in everyone if t["direction"] != "unavailable"]
    dirs = [t["direction"] for t in avail]
    bull, bear = dirs.count("bullish"), dirs.count("bearish")
    conflicts = []
    if bull and bear:
        up = [LABEL[t["timeframe"]] for t in avail if t["direction"] == "bullish"]
        dn = [LABEL[t["timeframe"]] for t in avail if t["direction"] == "bearish"]
        conflicts.append(f"{'/'.join(up)} bullish vs {'/'.join(dn)} bearish")
    for t in avail:
        if t["direction"] == "neutral":
            conflicts.append(f"{LABEL[t['timeframe']]} is {t['classification'].lower()}")

    if not avail:
        alignment, bias = "unavailable", "Unavailable"
    elif bull and bear:
        alignment, bias = "conflicting", "Mixed"
    elif bull or bear:
        side = "Bullish" if bull else "Bearish"
        full = len(avail) == len(everyone) and len(avail) >= 2 and (bull == len(avail) or bear == len(avail))
        alignment = "aligned" if full else "partial"
        bias = side if full else f"Leaning {side}"
    else:
        alignment, bias = "neutral", "Neutral"

    states = [t["structure_state"] for t in avail if t["structure_state"] != "undetermined"]
    if not states:
        struct = "undetermined"
    elif len(set(states)) > 1:
        struct = "conflicting"
    else:
        struct = "aligned" if len(states) == len(everyone) else "partial"

    parts = [f"{LABEL[t['timeframe']]} {(t['classification'] or 'unavailable').lower()}" for t in everyone]
    summary = f"{', '.join(parts)}. Alignment: {alignment}; overall bias: {bias.lower()}."
    return dict(primary=primary, higher=higher, alignment=alignment, structural_alignment=struct, bias=bias,
                conflicts=conflicts, summary=summary)
