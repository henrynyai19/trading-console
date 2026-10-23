import pytest

from app.analysis.models import AnalysisResponse, Freshness, MTF, TFTrend, TrendComponent, TrendInfo, Zone
from app.providers.base import Candle
from app.setups import SetupConfig, TradeSetup, generate_setups
from app.backtesting import run_backtest, validate_backtest_candles
from tests.analysis_helpers import zigzag


def _analysis(candles, status="ok"):
    last = candles[-1]
    if status != "ok":
        return AnalysisResponse(instrument="XAUUSD", provider_symbol="XAU_USD", timeframe="15m",
                                analysis_time=last.time + 900, status="stale",
                                freshness=Freshness(status="stale", last_closed_time=last.time,
                                                    last_close_at=last.time + 900, age_seconds=900,
                                                    trading_age_seconds=900), candles_used=len(candles), config={})
    return AnalysisResponse(
        instrument="XAUUSD", provider_symbol="XAU_USD", timeframe="15m", analysis_time=last.time + 900,
        status=status, freshness=Freshness(status="fresh", last_closed_time=last.time,
                                           last_close_at=last.time + 900, age_seconds=0, trading_age_seconds=0),
        candles_used=len(candles), config={},
        trend=TrendInfo(classification="Bullish", score=40, components=[TrendComponent(name="structure", points=20,
            max_points=40, detail="rising structure")], explanation=["higher lows"], structure_state="bullish"),
        zones=[Zone(id="support-1", instrument="XAUUSD", timeframe="15m", type="support", origin_type="support",
                    upper=101, lower=100, strength=80, touches=3, start_time=candles[10].time,
                    created_at=candles[10].time, last_interaction=last.time, status="active")],
        mtf=MTF(primary=TFTrend(timeframe="15m", direction="bullish", score=40, structure_state="bullish"),
                higher=[], alignment="aligned", structural_alignment="aligned", bias="Bullish", conflicts=[], summary="aligned"))


def test_valid_closed_candle_retest_emits_plan_with_r_targets():
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.4, 100.4, 101.2, 1, True))
    result = generate_setups("XAUUSD", "15m", candles, _analysis(candles))
    assert result.status == "ok"
    assert result.setups
    setup = result.setups[0]
    assert setup.direction == "long"
    assert [target.r_multiple for target in setup.targets] == [1.5, 2.0, 3.0]
    assert setup.targets[1].price > setup.planned_entry > setup.stop_loss
    assert setup.status == "confirmed"
    assert setup.confirmed_at == candles[-1].time + 900
    assert setup.confirmed_snapshot and setup.risk_distance == round(setup.planned_entry - setup.stop_loss, 2)
    assert not setup.monetary_risk_available
    assert setup.data_freshness == "fresh"


def test_short_resistance_setup_has_correct_levels_and_rr():
    candles = [Candle(i * 900, 100.4, 101.2, 99.8, 100.3, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.8, 101.2, 99.8, 99.6, 1, True))
    a = _analysis(candles)
    a.trend.structure_state = "bearish"
    a.mtf.alignment = "partial"
    a.zones[0].type = "resistance"
    a.zones[0].origin_type = "resistance"
    result = generate_setups("XAUUSD", "15m", candles, a)
    assert result.setups
    setup = result.setups[0]
    assert setup.direction == "short" and setup.setup_type == "resistance_retest"
    assert setup.stop_loss > setup.planned_entry
    assert all(t.price < setup.planned_entry for t in setup.targets)
    assert [t.r_multiple for t in setup.targets] == [1.5, 2, 3]


def test_invalid_stop_is_rejected_and_repeated_detection_keeps_same_id():
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.4, 100.4, 101.2, 1, True))
    a = _analysis(candles)
    setup = generate_setups("XAUUSD", "15m", candles, a).setups[0]
    assert generate_setups("XAUUSD", "15m", candles, a).setups[0].id == setup.id
    invalid = setup.model_dump()
    invalid["stop_loss"] = setup.planned_entry + 1
    with pytest.raises(ValueError, match="long stop"):
        TradeSetup.model_validate(invalid)


def test_mtf_conflict_reduces_score_and_can_block_confirmation():
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.4, 100.4, 101.2, 1, True))
    aligned = _analysis(candles)
    baseline = generate_setups("XAUUSD", "15m", candles, aligned).setups[0]
    conflict = _analysis(candles)
    conflict.mtf.alignment = "conflicting"
    conflict.mtf.conflicts = ["1h structure conflicts with 15m direction"]
    reduced = generate_setups("XAUUSD", "15m", candles, conflict).setups[0]
    blocked = generate_setups("XAUUSD", "15m", candles, conflict, cfg=SetupConfig(block_conflicting_mtf=True)).setups[0]
    assert reduced.quality_score < baseline.quality_score
    assert reduced.status == "confirmed" and reduced.mtf_conflicts
    assert blocked.status == "awaiting_confirmation" and not blocked.lower_timeframe_confirmation


def test_lifecycle_requires_closed_candles_and_resolves_same_bar_stop_first():
    from app.setups import advance_setup
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.4, 100.4, 101.2, 1, True))
    setup = generate_setups("XAUUSD", "15m", candles, _analysis(candles)).setups[0]
    ignored = advance_setup(setup, Candle(60 * 900, 101, 102, 99, 101, 1, False), 60 * 900 + 900)
    assert ignored.status == "confirmed"
    active = advance_setup(setup, Candle(60 * 900, 102, 102.2, 101.5, 102, 1, True), 60 * 900 + 900)
    assert active.status == "active_reference"
    touched = advance_setup(active, Candle(61 * 900, 101.2, 101.4, 100.5, 100.8, 1, True), 61 * 900 + 900)
    assert touched.status == "entry_touched"
    ambiguous = advance_setup(touched, Candle(62 * 900, 100, 103, setup.stop_loss - 0.1, 102, 1, True), 62 * 900 + 900)
    assert ambiguous.status == "stopped_out"
    assert "same candle" in ambiguous.lifecycle_events[-1].reason
    later = advance_setup(ambiguous, Candle(63 * 900, 100, 104, 99, 103, 1, True), 63 * 900 + 900)
    assert later is ambiguous


def test_target_lifecycle_advances_sequentially_and_stays_reference_only():
    from app.setups import advance_setup
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.4, 100.4, 101.2, 1, True))
    setup = generate_setups("XAUUSD", "15m", candles, _analysis(candles)).setups[0]
    setup.status = "entry_touched"
    one = advance_setup(setup, Candle(60 * 900, 101.8, setup.targets[0].price + 0.01, 101.4, 102, 1, True), 61 * 900)
    two = advance_setup(one, Candle(61 * 900, 102, setup.targets[1].price + 0.01, 101.9, 102.2, 1, True), 62 * 900)
    three = advance_setup(two, Candle(62 * 900, 103, setup.targets[2].price + 0.01, 102.9, 103.2, 1, True), 63 * 900)
    assert [one.status, two.status, three.status] == ["target_1_reached", "target_2_reached", "target_3_reached"]
    assert all("no broker fill" in event.reason for item in (one, two, three) for event in item.lifecycle_events if "target_" in event.status)


def test_pending_confirmation_expires_without_lookahead():
    from app.setups import advance_setup
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(59)]
    candles.append(Candle(59 * 900, 100.6, 101.1, 100.4, 100.8, 1, True))
    setup = generate_setups("XAUUSD", "15m", candles, _analysis(candles)).setups[0]
    assert setup.status == "awaiting_confirmation" and setup.confirmed_at is None
    bar = Candle(60 * 900, 100.7, 100.9, 100.3, 100.6, 1, True)
    expired = advance_setup(setup, bar, setup.expires_at)
    assert expired.status == "expired" and expired.confirmed_snapshot is None


def test_stale_analysis_never_emits_trade_setup():
    candles = [Candle(i * 900, 100.5, 101.2, 99.9, 100.6, 1, True) for i in range(60)]
    a = _analysis(candles, status="stale")
    a.freshness.status = "stale"
    result = generate_setups("XAUUSD", "15m", candles, a)
    assert result.status == "stale"
    assert result.setups == []


def test_backtest_entries_follow_their_signal_close_and_costs_are_visible():
    legs = [110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88]
    candles = zigzag(legs, bars=8, tf="15m", end=1_800_000_000)
    free = run_backtest("XAUUSD", "15m", candles, risk_reward=2.0, min_swing_strength=0)
    costed = run_backtest("XAUUSD", "15m", candles, risk_reward=2.0, spread_ticks=2, slippage_ticks=1, min_swing_strength=0)
    assert free.candles_used >= 60
    assert free.trades
    assert all(t.entry_time >= t.signal_time for t in free.trades)
    assert "zero" in free.warnings[0]
    assert costed.spread_ticks == 2
    assert costed.slippage_ticks == 1
    assert costed.trades[0].entry_price != free.trades[0].entry_price


def test_phase6_backtest_metrics_costs_and_periods_are_reproducible():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    free = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0,
                        in_sample_percent=60, validation_percent=20)
    again = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0,
                         in_sample_percent=60, validation_percent=20)
    costed = run_backtest("XAUUSD", "15m", candles, spread_ticks=2, slippage_ticks=1,
                          commission_ticks_per_side=0.5, min_swing_strength=0)
    assert free.model_dump() == again.model_dump()
    assert [p.name for p in free.periods] == ["in_sample", "validation", "out_of_sample"]
    assert sum(p.candles for p in free.periods) == free.candles_used
    assert free.equity_curve and free.drawdown_curve
    assert free.by_instrument[0].key == "XAUUSD" and free.by_timeframe[0].key == "15m"
    assert free.executed_trades == len(free.trades) and free.eligible_setups >= free.executed_trades
    assert {trade.direction for trade in free.trades} == {"long", "short"}
    assert free.candle_fingerprint and free.strategy_version == "structure-breaks-v1"
    assert costed.total_cost_r > free.total_cost_r and costed.net_r <= free.net_r
    assert costed.trades[0].cost_r > 0
    assert all(t.entry_time >= t.signal_time for t in free.trades)


def test_phase6_date_range_uses_prior_warmup_but_counts_only_requested_candles():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    first, last = candles[80].time, candles[140].time
    report = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0, start_time=first, end_time=last)
    assert report.requested_start == first and report.requested_end == last
    assert report.candles_used == 61
    assert report.data_quality["warmup_candles"] == 80
    assert all(first <= p.time <= last for p in report.equity_curve)


def test_phase6_future_candles_do_not_change_earlier_signal_decisions():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    prefix = candles[:120]
    full_report = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0)
    prefix_report = run_backtest("XAUUSD", "15m", prefix, min_swing_strength=0)
    full_decisions = [(t.signal_time, t.direction, t.entry_time, t.stop_loss, t.target_price)
                      for t in full_report.trades if t.signal_time <= prefix[-1].time]
    prefix_decisions = [(t.signal_time, t.direction, t.entry_time, t.stop_loss, t.target_price)
                        for t in prefix_report.trades]
    assert full_decisions == prefix_decisions


def test_backtest_rejects_duplicate_ordered_or_invalid_ohlc_history():
    good = [Candle(i * 900, 100, 101, 99, 100.5, 1, True) for i in range(60)]
    assert len(validate_backtest_candles(good)) == 60
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_backtest_candles([good[0], good[0], *good[1:]])
    with pytest.raises(ValueError, match="OHLC"):
        validate_backtest_candles([*good[:-1], Candle(good[-1].time, 100, 98, 99, 100, 1, True)])


def test_phase6_zero_trade_metrics_are_null_and_not_infinite():
    flat = [Candle(i * 900, 100, 100.1, 99.9, 100, 0, True) for i in range(80)]
    report = run_backtest("XAUUSD", "15m", flat)
    assert report.closed_trades == 0 and report.wins == report.losses == 0
    assert report.win_rate_percent is None and report.expectancy_r is None and report.profit_factor is None
    assert report.net_r == 0 and not report.equity_curve
