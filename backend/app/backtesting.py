"""Deterministic closed-candle structural backtest; never places orders."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Literal
from uuid import UUID

import numpy as np
from pydantic import BaseModel, Field

from .analysis.indicators import atr as atr_fn
from .analysis.structure_analyzer import analyze_structure
from .analysis.swing_detector import arrays, detect_swings
from .providers.base import INSTRUMENTS, TF_SECONDS, Candle


class BacktestTrade(BaseModel):
    instrument: str = "XAUUSD"
    timeframe: str = "1h"
    direction: Literal["long", "short"]
    setup_type: Literal["breakout", "reversal"]
    signal_time: int
    confirmation_time: int | None = None
    entry_time: int
    exit_time: int | None = None
    entry_price: float
    stop_loss: float
    target_price: float
    exit_price: float | None = None
    outcome: Literal["target", "stop", "open"]
    realized_r: float | None = None
    gross_result_r: float | None = None
    cost_r: float = 0.0
    max_favourable_r: float = 0.0
    max_adverse_r: float = 0.0
    duration_seconds: int | None = None
    evaluation_period: Literal["in_sample", "validation", "out_of_sample"] = "in_sample"
    lifecycle_events: list[str] = Field(default_factory=list)


class EquityPoint(BaseModel):
    time: int
    equity_r: float
    drawdown_r: float


class PeriodSummary(BaseModel):
    name: Literal["in_sample", "validation", "out_of_sample"]
    start_time: int | None
    end_time: int | None
    candles: int
    trades: int
    net_r: float
    expectancy_r: float | None


class GroupSummary(BaseModel):
    key: str
    trades: int
    net_r: float
    expectancy_r: float | None


class BacktestResponse(BaseModel):
    backtest_id: UUID | None = None
    instrument: str
    provider_symbol: str
    timeframe: str
    data_source: str = "unknown"
    data_mode: Literal["configured_provider", "test_mock_endpoint"] = "configured_provider"
    strategy: Literal["structure_breaks"] = "structure_breaks"
    strategy_version: str = "structure-breaks-v1"
    strategy_configuration: dict = Field(default_factory=dict)
    candle_fingerprint: str = ""
    candles_used: int
    eligible_setups: int = 0
    executed_trades: int = 0
    result_persisted: bool = False
    risk_reward: Literal[1.5, 2.0, 3.0]
    spread_ticks: float
    slippage_ticks: float
    commission_ticks_per_side: float = 0.0
    closed_trades: int
    wins: int
    losses: int
    win_rate_percent: float | None
    net_r: float
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_r: float
    trades: list[BacktestTrade]
    warnings: list[str] = []
    requested_start: int | None = None
    requested_end: int | None = None
    excluded_unclosed_candles: int = 0
    data_quality: dict = Field(default_factory=dict)
    gross_r: float = 0.0
    total_cost_r: float = 0.0
    average_win_r: float | None = None
    average_loss_r: float | None = None
    breakeven_trades: int = 0
    longest_losing_streak: int = 0
    average_duration_seconds: float | None = None
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    drawdown_curve: list[EquityPoint] = Field(default_factory=list)
    by_instrument: list[GroupSummary] = Field(default_factory=list)
    by_timeframe: list[GroupSummary] = Field(default_factory=list)
    by_setup_type: list[GroupSummary] = Field(default_factory=list)
    by_direction: list[GroupSummary] = Field(default_factory=list)
    over_time: list[EquityPoint] = Field(default_factory=list)
    periods: list[PeriodSummary] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def run_backtest(symbol: str, tf: str, raw_candles: list[Candle], risk_reward: float = 2.0,
                 spread_ticks: float = 0.0, slippage_ticks: float = 0.0, left: int = 3,
                 right: int = 3, min_swing_strength: float = 10.0, provider_symbol: str | None = None,
                 in_sample_percent: int = 60, validation_percent: int = 20,
                 commission_ticks_per_side: float = 0.0, start_time: int | None = None,
                 end_time: int | None = None) -> BacktestResponse:
    if risk_reward not in (1.5, 2.0, 3.0):
        raise ValueError("risk_reward must be 1.5, 2.0 or 3.0")
    if any(not np.isfinite(v) or v < 0 for v in (spread_ticks, slippage_ticks, commission_ticks_per_side)):
        raise ValueError("spread, slippage and commission must be finite and non-negative")
    if in_sample_percent < 20 or validation_percent < 0 or in_sample_percent + validation_percent >= 100:
        raise ValueError("in_sample_percent must be >=20 and validation_percent must leave a non-empty out-of-sample period")
    excluded_unclosed = sum(not c.complete or c.provisional for c in raw_candles)
    candles = validate_backtest_candles(raw_candles)
    if start_time is not None and end_time is not None and end_time <= start_time:
        raise ValueError("end_time must be later than start_time")
    if end_time is not None:
        candles = [c for c in candles if c.time <= end_time]
    evaluation_start = start_time if start_time is not None else (candles[0].time if candles else 0)
    evaluation = [c for c in candles if c.time >= evaluation_start and (end_time is None or c.time <= end_time)]
    if len(evaluation) > 5000:
        raise ValueError("The selected date range exceeds 5,000 evaluated candles")
    if start_time is not None:
        warmup = [c for c in candles if c.time < evaluation_start][-100:]
        candles = warmup + evaluation
    else:
        candles = candles[-5000:]
        evaluation = candles
    if len(candles) < 60:
        raise ValueError("At least 60 valid closed candles are required")
    if len(evaluation) < 60:
        raise ValueError("At least 60 valid closed candles are required inside the selected date range")

    step = TF_SECONDS[tf]
    times, _, highs, lows, closes = arrays(candles)
    atrs = atr_fn(highs, lows, closes)
    swings = detect_swings(candles, step, left=left, right=right, min_strength=min_swing_strength, atr_arr=atrs)
    structure = analyze_structure(times, closes, atrs, swings, step)
    by_idx: dict[int, list] = {}
    for event in structure.events:
        by_idx.setdefault(event.break_idx, []).append(event)

    tick = INSTRUMENTS[symbol]["tick"]
    digits = INSTRUMENTS[symbol]["digits"]
    cost = tick * (spread_ticks / 2 + slippage_ticks + commission_ticks_per_side)
    trades: list[BacktestTrade] = []
    eligible_setups = 0
    next_free = 0
    evaluation_indices = [i for i, c in enumerate(candles) if c.time >= evaluation_start]
    eval_start_idx, eval_end_idx = evaluation_indices[0], evaluation_indices[-1]
    for signal_idx, events in sorted(by_idx.items()):
        if signal_idx < eval_start_idx or signal_idx > eval_end_idx or signal_idx + 1 > eval_end_idx or signal_idx < next_free:
            continue
        for event in events:
            direction = "long" if event.direction == "bullish" else "short"
            opposite_kind = "low" if direction == "long" else "high"
            prior = [s for s in swings if s.kind == opposite_kind and s.confirmed_idx < signal_idx]
            if not prior:
                continue
            anchor = prior[-1]
            buffer = max(atrs[signal_idx] * 0.25, tick)
            stop = anchor.price - buffer if direction == "long" else anchor.price + buffer
            sign = 1 if direction == "long" else -1
            entry_idx = signal_idx + 1
            entry_reference = float(candles[entry_idx].open)
            entry = float(entry_reference + sign * cost)
            risk = sign * (entry_reference - stop)
            if risk <= tick or risk > 6 * max(atrs[signal_idx], tick):
                continue
            eligible_setups += 1
            target = entry + sign * risk * risk_reward
            outcome: Literal["target", "stop", "open"] = "open"
            exit_price = None
            exit_reference = None
            exit_idx = None
            max_favourable = 0.0
            max_adverse = 0.0
            for j in range(entry_idx, eval_end_idx + 1):
                c = candles[j]
                favourable = (c.high - entry_reference) if direction == "long" else (entry_reference - c.low)
                adverse = (entry_reference - c.low) if direction == "long" else (c.high - entry_reference)
                max_favourable = max(max_favourable, favourable / risk)
                max_adverse = max(max_adverse, adverse / risk)
                stop_hit = c.low <= stop if direction == "long" else c.high >= stop
                target_hit = c.high >= target if direction == "long" else c.low <= target
                # If both barriers fall inside one candle, conservatively assume the stop was hit first.
                if stop_hit:
                    outcome, exit_idx = "stop", j
                    exit_reference = (min(c.open, stop) if direction == "long" else max(c.open, stop))
                    exit_price = exit_reference - sign * cost
                    break
                if target_hit:
                    outcome, exit_idx = "target", j
                    exit_reference = target
                    exit_price = exit_reference - sign * cost
                    break
            gross = round(sign * (exit_reference - entry_reference) / risk, 4) if exit_reference is not None else None
            cost_r = round(2 * cost / risk, 4) if exit_reference is not None else 0.0
            realized = round(gross - cost_r, 4) if gross is not None else None
            trades.append(BacktestTrade(instrument=symbol, timeframe=tf, direction=direction,
                                        setup_type="breakout" if event.kind == "BOS" else "reversal",
                                        signal_time=event.confirmed_at, confirmation_time=event.confirmed_at,
                                        entry_time=candles[entry_idx].time,
                                        exit_time=candles[exit_idx].time if exit_idx is not None else None,
                                        entry_price=round(entry, digits), stop_loss=round(stop, digits),
                                        target_price=round(target, digits), exit_price=round(exit_price, digits) if exit_price is not None else None,
                                        outcome=outcome, realized_r=realized, gross_result_r=gross, cost_r=cost_r,
                                        max_favourable_r=round(max_favourable, 4), max_adverse_r=round(max_adverse, 4),
                                        duration_seconds=(candles[exit_idx].time - candles[entry_idx].time) if exit_idx is not None else None,
                                        lifecycle_events=["signal confirmed at candle close", "entry at next candle open"] +
                                        ([f"exit: {outcome}"] if exit_reference is not None else ["open at end of data"])))
            next_free = (exit_idx + 1) if exit_idx is not None else len(candles)
            break

    closed = [t for t in trades if t.realized_r is not None]
    values = [float(t.realized_r) for t in closed]
    gross_values = [float(t.gross_result_r or 0) for t in closed]
    wins = sum(v > 0 for v in values)
    gains, loss_r_sum = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
    loss_count = sum(v < 0 for v in values)
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    warnings = ["Spread, slippage and commission are zero; results omit trading costs."] if cost == 0 else []
    warnings.append("Candle OHLC has no bid/ask history. Spread is an assumed per-side cost; slippage and commission are also charged per side. Costs are included once at entry and once at exit.")
    warnings.append("Signals are evaluated at the break candle close and enter at the next candle open. Same-candle stop/target ambiguity resolves to the stop; gaps through stops exit at the worse candle open.")
    if len(evaluation) < 200:
        warnings.append("Small history window: results may be unstable and are not statistically significant.")
    gaps = [candles[i].time - candles[i - 1].time - step for i in range(1, len(candles)) if candles[i].time - candles[i - 1].time > step]
    if gaps:
        warnings.append(f"Historical candles contain {len(gaps)} time gaps; closures and missing provider candles cannot be distinguished automatically.")
    if loss_r_sum == 0 and gains > 0:
        warnings.append("No losing closed trades: profit factor is undefined rather than infinite.")

    in_end = max(1, int(len(evaluation) * in_sample_percent / 100))
    val_end = min(len(evaluation) - 1, in_end + int(len(evaluation) * validation_percent / 100))
    cut1, cut2 = evaluation[in_end - 1].time, evaluation[val_end - 1].time
    for trade in trades:
        trade.evaluation_period = "in_sample" if trade.entry_time <= cut1 else "validation" if trade.entry_time <= cut2 else "out_of_sample"
    equity_points: list[EquityPoint] = []
    equity = peak = 0.0
    losing_streak = max_losing_streak = 0
    for trade in closed:
        equity += float(trade.realized_r or 0)
        peak = max(peak, equity)
        dd = peak - equity
        equity_points.append(EquityPoint(time=trade.exit_time or trade.entry_time, equity_r=round(equity, 4), drawdown_r=round(dd, 4)))
        if (trade.realized_r or 0) < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    def group_by(field_name: str) -> list[GroupSummary]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for trade in closed:
            buckets[str(getattr(trade, field_name))].append(float(trade.realized_r or 0))
        return [GroupSummary(key=k, trades=len(v), net_r=round(sum(v), 4), expectancy_r=round(sum(v) / len(v), 4) if v else None)
                for k, v in sorted(buckets.items())]
    periods = []
    for name, first, last in (("in_sample", 0, in_end), ("validation", in_end, val_end), ("out_of_sample", val_end, len(evaluation))):
        selected = [t for t in closed if t.evaluation_period == name]
        vals = [float(t.realized_r or 0) for t in selected]
        periods.append(PeriodSummary(name=name, start_time=evaluation[first].time if first < len(evaluation) else None,
                                     end_time=evaluation[last - 1].time if last > first else None, candles=last-first,
                                     trades=len(vals), net_r=round(sum(vals), 4), expectancy_r=round(sum(vals)/len(vals), 4) if vals else None))
    candle_fingerprint = hashlib.sha256(json.dumps(
        [[c.time, c.open, c.high, c.low, c.close, c.volume] for c in evaluation], separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
    return BacktestResponse(
        instrument=symbol, provider_symbol=provider_symbol or INSTRUMENTS[symbol]["provider"], timeframe=tf, candles_used=len(evaluation),
        eligible_setups=eligible_setups, executed_trades=len(trades), result_persisted=False,
        strategy="structure_breaks", strategy_version="structure-breaks-v1",
        strategy_configuration={"risk_reward": risk_reward, "left": left, "right": right,
                                "min_swing_strength": min_swing_strength, "in_sample_percent": in_sample_percent,
                                "validation_percent": validation_percent, "spread_ticks": spread_ticks,
                                "slippage_ticks": slippage_ticks, "commission_ticks_per_side": commission_ticks_per_side,
                                "max_concurrent_trades": 1, "atr_stop_buffer": 0.25},
        candle_fingerprint=candle_fingerprint,
        risk_reward=risk_reward, spread_ticks=spread_ticks, slippage_ticks=slippage_ticks,
        commission_ticks_per_side=commission_ticks_per_side,
        closed_trades=len(closed), wins=wins, losses=loss_count,
        win_rate_percent=round(100 * wins / len(closed), 2) if closed else None,
        net_r=round(sum(values), 4), expectancy_r=round(sum(values) / len(values), 4) if values else None,
        profit_factor=round(gains / loss_r_sum, 4) if loss_r_sum else None,
        max_drawdown_r=round(drawdown, 4), trades=trades, warnings=warnings,
        gross_r=round(sum(gross_values), 4), total_cost_r=round(sum(t.cost_r for t in closed), 4),
        average_win_r=round(gains / wins, 4) if wins else None,
        average_loss_r=round(loss_r_sum / loss_count, 4) if loss_count else None,
        breakeven_trades=sum(v == 0 for v in values), longest_losing_streak=max_losing_streak,
        average_duration_seconds=round(sum(t.duration_seconds or 0 for t in closed) / len(closed), 2) if closed else None,
        equity_curve=equity_points, drawdown_curve=equity_points, over_time=equity_points,
        by_instrument=group_by("instrument"), by_timeframe=group_by("timeframe"),
        by_setup_type=group_by("setup_type"), by_direction=group_by("direction"), periods=periods,
        requested_start=evaluation[0].time, requested_end=evaluation[-1].time,
        excluded_unclosed_candles=excluded_unclosed,
        data_quality={"valid_closed_candles": len(evaluation), "warmup_candles": len(candles)-len(evaluation),
                      "gap_count": len(gaps), "gap_durations_seconds": gaps[:100], "timestamps_utc": True},
        assumptions=["One simulated trade at a time; no leverage or account-currency accounting.",
                     "Out-of-sample is a chronological holdout; no parameter optimizer uses its results.",
                     "Gross/net results are normalized by initial price risk (R), with assumed per-side costs charged once at each fill."])


def validate_backtest_candles(raw: list[Candle]) -> list[Candle]:
    """Reject malformed candles and duplicate/out-of-order timestamps; return completed provider candles only."""
    if len(raw) > 50_000:
        raise ValueError("Historical input exceeds the 50,000-candle validation limit")
    times = [c.time for c in raw]
    if times != sorted(times) or len(times) != len(set(times)):
        raise ValueError("Historical candle timestamps must be strictly increasing with no duplicates")
    selected = []
    for c in raw:
        values = (c.open, c.high, c.low, c.close, c.volume)
        if not all(np.isfinite(v) for v in values) or c.time < 0 or min(c.open, c.high, c.low, c.close) <= 0 or c.volume < 0:
            raise ValueError("Historical candles contain non-finite, non-positive, or invalid volume values")
        if c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close, c.high):
            raise ValueError("Historical candle OHLC values are inconsistent")
        if c.complete and not c.provisional:
            selected.append(c)
    return selected
