"""Closed-candle, rule-based setup analysis. This module never sends broker orders."""
from __future__ import annotations

import hashlib
import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from .analysis.indicators import atr as atr_fn
from .analysis.models import AnalysisResponse, Zone
from .providers.base import INSTRUMENTS, TF_SECONDS, Candle

SetupType = Literal["support_retest", "resistance_retest", "breakout_retest", "trend_continuation"]
SetupStatus = Literal[
    "developing", "awaiting_confirmation", "confirmed", "active_reference", "entry_touched",
    "target_1_reached", "target_2_reached", "target_3_reached", "stopped_out", "invalidated", "expired",
    "active", "target_touched",
]
TERMINAL = {"target_3_reached", "stopped_out", "invalidated", "expired", "target_touched"}


class SetupConfig(BaseModel):
    """Defaults: 0.25 ATR stop buffer, 1.5/2/3R targets, 12-bar expiry, MTF conflict reduces score."""
    stop_buffer_atr: float = Field(default=0.25, gt=0, le=2)
    target_multiples: tuple[float, ...] = (1.5, 2.0, 3.0)
    max_age_bars: int = Field(default=12, ge=1, le=100)
    block_conflicting_mtf: bool = False
    strong_opposing_zone: float = Field(default=60, ge=0, le=100)

    @field_validator("target_multiples")
    @classmethod
    def _targets_valid(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not values or any(not math.isfinite(x) or x <= 0 for x in values) or tuple(sorted(set(values))) != values:
            raise ValueError("target multiples must be strictly increasing positive finite values")
        return values


class SetupTarget(BaseModel):
    r_multiple: float = Field(gt=0)
    price: float

    @field_validator("price")
    @classmethod
    def _finite_price(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("target price must be finite and positive")
        return value


class SetupLifecycleEvent(BaseModel):
    status: SetupStatus
    at: int
    candle_time: int | None = None
    reason: str


class TradeSetup(BaseModel):
    id: str
    instrument: str
    provider_symbol: str
    timeframe: str
    setup_type: SetupType
    direction: Literal["long", "short"]
    status: SetupStatus
    quality_score: int = Field(ge=0, le=100, description="Rule-based quality score, not a probability")
    score_explanation: list[str] = Field(default_factory=list)
    caution_reasons: list[str] = Field(default_factory=list)
    created_at: int
    detected_at: int = 0
    confirmed_at: int | None = None
    data_time: int = 0
    data_freshness: Literal["fresh", "market_closed", "stale", "unknown"] = "unknown"
    expires_at: int
    reference_level: float | None = None
    invalidation_price: float
    entry_lower: float
    entry_upper: float
    planned_entry: float
    stop_loss: float
    targets: list[SetupTarget]
    risk_distance: float = Field(gt=0, description="Price units only; monetary risk is unavailable without contract and account-currency metadata")
    risk_currency: str | None = None
    monetary_risk_available: bool = False
    source_zone_id: str | None = None
    source_event_id: str | None = None
    higher_timeframes: list[dict[str, Any]] = Field(default_factory=list)
    higher_timeframe_direction: str = "unavailable"
    lower_timeframe_confirmation: bool = False
    mtf_alignment: str = "unavailable"
    mtf_conflicts: list[str] = Field(default_factory=list)
    opposing_zone_type: str | None = None
    opposing_zone_price: float | None = None
    opposing_zone_strength: float | None = None
    confirmation_explanation: str = ""
    invalidation_explanation: str = ""
    explanation: str
    last_processed_time: int | None = None
    confirmed_snapshot: dict[str, Any] | None = None
    lifecycle_events: list[SetupLifecycleEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _prices_and_levels(self):
        # Previous persisted payloads predate explicit reference and confirmation fields.
        if self.reference_level is None:
            self.reference_level = self.planned_entry
        values = [self.reference_level, self.invalidation_price, self.entry_lower, self.entry_upper,
                  self.planned_entry, self.stop_loss, self.risk_distance]
        if not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError("setup levels and risk must be finite and positive")
        if self.entry_lower > self.entry_upper or not self.entry_lower <= self.planned_entry <= self.entry_upper:
            raise ValueError("planned entry must be inside an ordered entry range")
        if self.direction == "long" and self.stop_loss >= self.planned_entry:
            raise ValueError("long stop must be below planned entry")
        if self.direction == "short" and self.stop_loss <= self.planned_entry:
            raise ValueError("short stop must be above planned entry")
        measured = abs(self.planned_entry - self.stop_loss)
        if measured <= 0 or abs(measured - self.risk_distance) > max(1e-8, measured * 1e-6):
            raise ValueError("risk distance must match planned entry to stop")
        for target in self.targets:
            if self.direction == "long" and target.price <= self.planned_entry:
                raise ValueError("long targets must be above planned entry")
            if self.direction == "short" and target.price >= self.planned_entry:
                raise ValueError("short targets must be below planned entry")
            measured_r = abs(target.price - self.planned_entry) / self.risk_distance
            if abs(measured_r - target.r_multiple) > 0.03:
                raise ValueError("target price and R multiple are inconsistent")
        return self


class SetupResponse(BaseModel):
    instrument: str
    provider_symbol: str
    timeframe: str
    status: Literal["ok", "insufficient_data", "stale"]
    reason: str | None = None
    analysis_time: int
    data_time: int | None = None
    data_freshness: Literal["fresh", "market_closed", "stale", "unknown"] = "unknown"
    current_price: float | None = None
    warnings: list[str] = Field(default_factory=list)
    setups: list[TradeSetup] = Field(default_factory=list)
    history_persistent: bool = False


def _snapshot(setup: TradeSetup, confirmed_at: int) -> dict[str, Any]:
    return {"confirmed_at": confirmed_at, "reference_level": setup.reference_level,
            "entry_lower": setup.entry_lower, "entry_upper": setup.entry_upper,
            "planned_entry": setup.planned_entry, "stop_loss": setup.stop_loss,
            "targets": [t.model_dump(mode="json") for t in setup.targets],
            "risk_distance": setup.risk_distance, "quality_score": setup.quality_score,
            "source_zone_id": setup.source_zone_id, "source_event_id": setup.source_event_id}


def advance_setup(setup: TradeSetup, candle: Candle, now: int) -> TradeSetup:
    """Advance from one new completed candle. Same-bar stop/target ambiguity resolves to the stop."""
    if setup.status in TERMINAL or not candle.complete or candle.provisional:
        return setup
    if setup.last_processed_time is not None and candle.time <= setup.last_processed_time:
        return setup

    status = setup.status
    at = candle.time + TF_SECONDS[setup.timeframe]
    reason: str | None = None
    confirmed_at, snapshot = setup.confirmed_at, setup.confirmed_snapshot
    is_long = setup.direction == "long"
    stop_hit = candle.low <= setup.stop_loss if is_long else candle.high >= setup.stop_loss
    target_hits = [i for i, target in enumerate(setup.targets)
                   if (candle.high >= target.price if is_long else candle.low <= target.price)]
    entry_hit = candle.low <= setup.entry_upper and candle.high >= setup.entry_lower

    if status in ("awaiting_confirmation", "developing"):
        if stop_hit:
            status, reason = "invalidated", "Structural invalidation crossed before confirmation."
        elif now >= setup.expires_at:
            status, reason = "expired", "Confirmation did not occur before expiry."
        else:
            confirmed = candle.close > setup.reference_level and candle.close > candle.open if is_long else \
                candle.close < setup.reference_level and candle.close < candle.open
            blocked = setup.mtf_alignment == "conflicting" and bool(setup.mtf_conflicts)
            if confirmed and not blocked:
                status, confirmed_at = "confirmed", at
                reason = "A completed candle confirmed rejection at the setup reference."
                snapshot = _snapshot(setup, at)
    elif status == "confirmed":
        if now >= setup.expires_at:
            status, reason = "expired", "The confirmed entry reference expired before it could be acted on."
        else:
            status, reason = "active_reference", "The entry reference is active; no broker fill is implied."
    elif status in ("active", "active_reference"):
        if stop_hit:
            status, reason = "invalidated", "Structural invalidation crossed before the entry reference was touched."
        elif now >= setup.expires_at:
            status, reason = "expired", "Active entry reference expired."
        elif entry_hit:
            status, reason = "entry_touched", "Entry reference range touched; this does not prove a broker fill."
        elif target_hits:
            status, reason = "expired", "Price reached a target before returning to the entry reference; setup missed."
    elif status in ("entry_touched", "target_1_reached", "target_2_reached"):
        if stop_hit and target_hits:
            status, reason = "stopped_out", "Stop and target were both inside the same candle; conservatively recorded as stopped out."
        elif stop_hit:
            status, reason = "stopped_out", "A completed candle crossed the structural stop reference."
        elif target_hits:
            highest = max(target_hits) + 1
            previous_target = {"entry_touched": 0, "target_1_reached": 1, "target_2_reached": 2}[status]
            if highest > previous_target:
                status = f"target_{highest}_reached"
                reason = f"Completed candle reached target {highest} by price; no broker fill is recorded."
        elif now >= setup.expires_at:
            status, reason = "expired", "The touched analytical reference expired."
    events = list(setup.lifecycle_events)
    if reason:
        events.append(SetupLifecycleEvent(status=status, at=at, candle_time=candle.time, reason=reason))
    return setup.model_copy(update={"status": status, "confirmed_at": confirmed_at,
                                    "confirmed_snapshot": snapshot, "last_processed_time": candle.time,
                                    "lifecycle_events": events})


def _setup_id(symbol: str, tf: str, kind: str, source: str, direction: str) -> str:
    return hashlib.sha256(f"{symbol}|{tf}|{kind}|{source}|{direction}".encode()).hexdigest()[:32]


def _score(zone_strength: float, trend_match: bool, mtf: str, recency: int) -> tuple[int, list[str]]:
    mtf_points = {"aligned": 20, "partial": 10, "conflicting": 0}.get(mtf, 0)
    zone_points = round(max(0, min(100, zone_strength)) * 0.2)
    trend_points = 15 if trend_match else 0
    age_points = 10 if recency <= 3 else 5 if recency <= 8 else 0
    parts = [
        (35, "A completed candle retested the structural reference"),
        (zone_points, f"Zone strength: {zone_points}/20"),
        (trend_points, "Primary trend agrees" if trend_match else "Primary trend is mixed or opposed"),
        (mtf_points, f"Higher-timeframe alignment: {mtf} ({mtf_points}/20)"),
        (age_points, f"Touch recency: {age_points}/10"),
    ]
    return max(0, min(100, sum(p for p, _ in parts))), [text for points, text in parts if points or "opposed" in text or "mixed" in text]


def _higher_fields(analysis: AnalysisResponse):
    mtf = analysis.mtf
    if not mtf:
        return [], "unavailable", "unavailable", []
    higher = [item.model_dump(mode="json") for item in mtf.higher]
    direction = ", ".join(f"{item.timeframe}: {item.direction}" for item in mtf.higher) or "unavailable"
    return higher, direction, mtf.alignment, list(mtf.conflicts)


def _opposing_zone(zones: list[Zone], direction: str, entry: float) -> Zone | None:
    eligible = [z for z in zones if z.status == "active" and
                ((direction == "long" and z.type == "resistance" and z.lower > entry) or
                 (direction == "short" and z.type == "support" and z.upper < entry))]
    return min(eligible, key=lambda z: z.lower - entry if direction == "long" else entry - z.upper, default=None)


def _make_setup(*, symbol: str, tf: str, provider_symbol: str, kind: SetupType, direction: Literal["long", "short"],
                source: str, source_zone: Zone | None, source_event: str | None, reference: float,
                entry_low: float, entry_high: float, entry: float, stop: float, atr: float, recency: int,
                trend_match: bool, analysis: AnalysisResponse, last: Candle, cfg: SetupConfig,
                confirmed: bool, detection_time: int) -> TradeSetup | None:
    digits = INSTRUMENTS[symbol]["digits"]
    entry, stop = round(entry, digits), round(stop, digits)
    risk = round(abs(entry - stop), digits)
    if risk <= 0 or risk > 4 * atr or (direction == "long" and stop >= entry) or (direction == "short" and stop <= entry):
        return None
    sign = 1 if direction == "long" else -1
    targets = [SetupTarget(r_multiple=r, price=round(entry + sign * risk * r, digits)) for r in cfg.target_multiples]
    higher, higher_direction, mtf, conflicts = _higher_fields(analysis)
    mtf_blocked = bool(confirmed and cfg.block_conflicting_mtf and mtf == "conflicting")
    if mtf_blocked:
        confirmed = False
    score, score_reasons = _score(source_zone.strength if source_zone else 50, trend_match, mtf, recency)
    caution: list[str] = []
    if not confirmed:
        caution.append("Completed-candle confirmation is pending; this is not an actionable reference.")
    if conflicts:
        caution.extend(conflicts)
    if mtf_blocked:
        caution.append("Higher-timeframe conflict blocks confirmation by configuration.")
    elif mtf == "unavailable":
        caution.append("Higher-timeframe analysis is unavailable; the setup receives no alignment points.")
    opposing = _opposing_zone(analysis.zones, direction, entry)
    opposing_price = (opposing.lower if direction == "long" else opposing.upper) if opposing else None
    if opposing and opposing.strength >= cfg.strong_opposing_zone:
        before_final = opposing_price < targets[-1].price if direction == "long" else opposing_price > targets[-1].price
        if before_final:
            caution.append(f"Strong opposing {opposing.type} zone at {opposing_price:.{digits}f} precedes the furthest R target; targets are shown without adjustment.")
    caution.append("Risk is price distance only; contract value, account currency and position sizing are unavailable.")
    confirmation_text = ("Closed candle rejected the level in the setup direction." if confirmed else
                         "Completed candle touched the reference but did not close beyond it directionally.")
    invalidation_text = f"Invalidated if price crosses {stop:.{digits}f}; same-candle stop/target ambiguity resolves to the stop."
    status: SetupStatus = "confirmed" if confirmed else "awaiting_confirmation"
    confirmed_at = last.time + TF_SECONDS[tf] if confirmed else None
    created_at = last.time + TF_SECONDS[tf]
    snapshot = None
    if confirmed_at is not None:
        snapshot = {"confirmed_at": confirmed_at, "reference_level": round(reference, digits),
                    "entry_lower": round(entry_low, digits), "entry_upper": round(entry_high, digits),
                    "planned_entry": entry, "stop_loss": stop,
                    "targets": [t.model_dump(mode="json") for t in targets], "risk_distance": risk,
                    "quality_score": score, "source_zone_id": source_zone.id if source_zone else None,
                    "source_event_id": source_event}
    return TradeSetup(
        id=_setup_id(symbol, tf, kind, source, direction), instrument=symbol, provider_symbol=provider_symbol,
        timeframe=tf, setup_type=kind, direction=direction, status=status, quality_score=score,
        score_explanation=score_reasons, caution_reasons=caution, created_at=created_at, detected_at=detection_time,
        confirmed_at=confirmed_at, data_time=last.time + TF_SECONDS[tf], data_freshness=analysis.freshness.status,
        expires_at=created_at + TF_SECONDS[tf] * cfg.max_age_bars, reference_level=round(reference, digits),
        invalidation_price=stop, entry_lower=round(entry_low, digits), entry_upper=round(entry_high, digits),
        planned_entry=entry, stop_loss=stop, targets=targets, risk_distance=risk, risk_currency=None,
        monetary_risk_available=False, source_zone_id=source_zone.id if source_zone else None,
        source_event_id=source_event, higher_timeframes=higher, higher_timeframe_direction=higher_direction,
        lower_timeframe_confirmation=confirmed, mtf_alignment=mtf, mtf_conflicts=conflicts,
        opposing_zone_type=opposing.type if opposing else None, opposing_zone_price=opposing_price,
        opposing_zone_strength=opposing.strength if opposing else None,
        confirmation_explanation=confirmation_text, invalidation_explanation=invalidation_text,
        explanation=f"{direction.title()} {kind.replace('_', ' ')} analytical reference from completed candles. It is not an order or fill.",
        last_processed_time=last.time, confirmed_snapshot=snapshot,
        lifecycle_events=[SetupLifecycleEvent(status=status, at=created_at, candle_time=last.time, reason=confirmation_text)])


def generate_setups(symbol: str, tf: str, candles: list[Candle], analysis: AnalysisResponse,
                    provider_symbol: str | None = None, cfg: SetupConfig = SetupConfig()) -> SetupResponse:
    """Generate stable, deduplicated references from validated closed candles."""
    provider_symbol = provider_symbol or INSTRUMENTS[symbol]["provider"]
    time_now, freshness = analysis.analysis_time, analysis.freshness.status
    closed = sorted((c for c in candles if c.complete and not c.provisional), key=lambda c: c.time)
    if analysis.status != "ok" or freshness not in ("fresh", "market_closed"):
        state = "stale" if analysis.status == "stale" or freshness == "stale" else "insufficient_data"
        return SetupResponse(instrument=symbol, provider_symbol=provider_symbol, timeframe=tf, status=state,
                             reason=analysis.reason or "Current closed-candle analysis is unavailable.",
                             analysis_time=time_now, data_freshness=freshness)
    if len(closed) < 60 or not analysis.trend:
        return SetupResponse(instrument=symbol, provider_symbol=provider_symbol, timeframe=tf,
                             status="insufficient_data", reason="At least 60 valid closed candles and a classified trend are required.",
                             analysis_time=time_now, data_freshness=freshness)

    last = closed[-1]
    highs = np.asarray([c.high for c in closed], dtype=float)
    lows = np.asarray([c.low for c in closed], dtype=float)
    closes = np.asarray([c.close for c in closed], dtype=float)
    atr = float(atr_fn(highs, lows, closes)[-1])
    if not math.isfinite(atr) or atr <= 0 or not all(math.isfinite(v) and v > 0 for v in (last.open, last.high, last.low, last.close)):
        return SetupResponse(instrument=symbol, provider_symbol=provider_symbol, timeframe=tf,
                             status="insufficient_data", reason="Candle prices or volatility are invalid.",
                             analysis_time=time_now, data_freshness=freshness)

    proposals: dict[str, TradeSetup] = {}
    trend_dir = analysis.trend.structure_state
    mtf = analysis.mtf.alignment if analysis.mtf else "unavailable"
    pad = 0.15 * atr
    for zone in analysis.zones:
        if zone.status != "active":
            continue
        touch_idx = next((i for i in range(len(closed) - 1, -1, -1) if closed[i].time == zone.last_interaction), None)
        if touch_idx is None:
            age_from_time = max(0, int((last.time - zone.last_interaction) / TF_SECONDS[tf]))
            age = age_from_time
        else:
            age = len(closed) - 1 - touch_idx
        if age > cfg.max_age_bars:
            continue
        if zone.type == "support":
            touched = last.low <= zone.upper + pad and last.high >= zone.lower
            direction: Literal["long", "short"] = "long"
            kind: SetupType = "support_retest"
            stop = zone.lower - cfg.stop_buffer_atr * atr
            rejection = last.close > zone.upper and last.close > last.open
        else:
            touched = last.high >= zone.lower - pad and last.low <= zone.upper
            direction = "short"
            kind = "resistance_retest"
            stop = zone.upper + cfg.stop_buffer_atr * atr
            rejection = last.close < zone.lower and last.close < last.open
        if not touched:
            continue
        trend_match = trend_dir == ("bullish" if direction == "long" else "bearish")
        if trend_match and mtf == "aligned":
            kind = "trend_continuation"
        candidate = _make_setup(
            symbol=symbol, tf=tf, provider_symbol=provider_symbol, kind=kind, direction=direction,
            source=f"{zone.id}:{zone.last_interaction}", source_zone=zone, source_event=None,
            reference=zone.upper if direction == "long" else zone.lower, entry_low=zone.lower, entry_high=zone.upper,
            entry=(zone.lower + zone.upper) / 2, stop=stop, atr=atr, recency=age, trend_match=trend_match,
            analysis=analysis, last=last, cfg=cfg, confirmed=rejection, detection_time=last.time + TF_SECONDS[tf])
        if candidate:
            proposals[candidate.id] = candidate

    for event in reversed(analysis.events):
        if event.kind != "BOS":
            continue
        event_idx = next((i for i, c in enumerate(closed) if c.time == event.break_time), None)
        if event_idx is None or len(closed) - 1 - event_idx > cfg.max_age_bars:
            continue
        direction = "long" if event.direction == "bullish" else "short"
        near = abs(last.close - event.level) <= 0.3 * atr
        retest = last.low <= event.level + 0.1 * atr if direction == "long" else last.high >= event.level - 0.1 * atr
        if not near or not retest:
            continue
        swing_kind = "low" if direction == "long" else "high"
        swing = next((s for s in reversed(analysis.swings) if s.kind == swing_kind and s.confirmed_at <= last.time), None)
        if not swing:
            continue
        stop = swing.price - cfg.stop_buffer_atr * atr if direction == "long" else swing.price + cfg.stop_buffer_atr * atr
        entry = float(event.level)
        rejection = last.close > event.level and last.close > last.open if direction == "long" else last.close < event.level and last.close < last.open
        trend_match = trend_dir == ("bullish" if direction == "long" else "bearish")
        candidate = _make_setup(
            symbol=symbol, tf=tf, provider_symbol=provider_symbol, kind="breakout_retest", direction=direction,
            source=event.id, source_zone=None, source_event=event.id, reference=entry,
            entry_low=entry - 0.1 * atr, entry_high=entry + 0.1 * atr, entry=entry, stop=stop, atr=atr,
            recency=len(closed) - 1 - event_idx, trend_match=trend_match, analysis=analysis, last=last,
            cfg=cfg, confirmed=rejection, detection_time=last.time + TF_SECONDS[tf])
        if candidate:
            proposals[candidate.id] = candidate

    setups = sorted(proposals.values(), key=lambda s: (s.quality_score, s.created_at), reverse=True)[:3]
    return SetupResponse(
        instrument=symbol, provider_symbol=provider_symbol, timeframe=tf, status="ok",
        analysis_time=time_now, data_time=last.time + TF_SECONDS[tf], data_freshness=freshness,
        current_price=last.close,
        warnings=["Setup levels are analytical references; they do not represent filled orders.",
                  "Monetary risk and position sizing are unavailable without account currency and contract-value metadata."],
        setups=setups)

