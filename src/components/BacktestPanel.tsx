"use client";
import { useState, type FormEvent } from "react";
import { fetchBacktest } from "@/lib/api";
import type { BacktestResponse, Symbol, Timeframe } from "@/lib/types";
import { DIGITS } from "@/lib/types";

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-2 border-b border-edge py-1.5 text-xs"><span className="text-mute">{label}</span><span className="num text-right">{value}</span></div>;
}

function EquityPlot({ report }: { report: BacktestResponse }) {
  const points = report.equity_curve;
  if (points.length < 2) return <p className="mt-2 text-xs text-mute">Equity and drawdown charts appear after at least two closed trades.</p>;
  const min = Math.min(0, ...points.map((p) => p.equity_r));
  const max = Math.max(0, ...points.map((p) => p.equity_r));
  const span = max - min || 1;
  const coords = points.map((p, i) => `${(i / (points.length - 1)) * 600},${100 - ((p.equity_r - min) / span) * 90}`).join(" ");
  const drawdown = points.map((p, i) => `${(i / (points.length - 1)) * 600},${100 - Math.min(p.drawdown_r / (report.max_drawdown_r || 1), 1) * 90}`).join(" ");
  return <div className="mt-3">
    <p className="text-xs font-medium">Cumulative net R · drawdown</p>
    <svg viewBox="0 0 600 110" role="img" aria-label="Backtest cumulative equity and drawdown in R" className="mt-1 h-28 w-full overflow-visible rounded bg-ink p-2">
      <polyline points={coords} fill="none" stroke="#3fb68b" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      <polyline points={drawdown} fill="none" stroke="#de6a68" strokeWidth="1.5" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
    </svg>
  </div>;
}

export function BacktestPanel({ symbol, timeframe }: { symbol: Symbol; timeframe: Timeframe }) {
  const stepSeconds: Record<Timeframe, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800 };
  const rangeDays = Math.max(1, Math.min(90, Math.floor(4000 * stepSeconds[timeframe] / 86400)));
  const today = new Date().toISOString().slice(0, 10);
  const [startDate, setStartDate] = useState(() => new Date(Date.now() - rangeDays * 86400_000).toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(today);
  const [riskReward, setRiskReward] = useState(2);
  const [spreadTicks, setSpreadTicks] = useState(0);
  const [slippageTicks, setSlippageTicks] = useState(0);
  const [commissionTicks, setCommissionTicks] = useState(0);
  const [inSamplePercent, setInSamplePercent] = useState(60);
  const [validationPercent, setValidationPercent] = useState(20);
  const [report, setReport] = useState<BacktestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function run(event: FormEvent) {
    event.preventDefault();
    setLoading(true); setError(null); setReport(null);
    const result = await fetchBacktest(symbol, timeframe, { startDate, endDate, riskReward, spreadTicks, slippageTicks,
      commissionTicks, inSamplePercent, validationPercent });
    if (result.ok) setReport(result.report); else setError(result.reason);
    setLoading(false);
  }

  return (
    <details className="mt-4 rounded border border-edge p-3" data-testid="backtest-panel">
      <summary className="cursor-pointer text-sm font-semibold">Historical backtest</summary>
      <p className="mt-2 text-xs text-mute">Structural BOS/CHoCH simulation. Signals use candle closes; entries use the next candle open. Results are historical analysis, never broker positions.</p>
      <form onSubmit={run} className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <label className="space-y-1">Start date (UTC)<input aria-label="Start date" className="w-full rounded border border-edge bg-ink p-2" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} required /></label>
        <label className="space-y-1">End date (UTC)<input aria-label="End date" className="w-full rounded border border-edge bg-ink p-2" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required /></label>
        <label className="space-y-1">Target<select aria-label="Target R" className="w-full rounded border border-edge bg-ink p-2" value={riskReward} onChange={(e) => setRiskReward(Number(e.target.value))}>
          <option value={1.5}>1.5R</option><option value={2}>2R</option><option value={3}>3R</option>
        </select></label>
        <label className="space-y-1">Spread (ticks / side model)<input aria-label="Spread ticks" className="w-full rounded border border-edge bg-ink p-2" type="number" min={0} max={1000} step={0.1} value={spreadTicks} onChange={(e) => setSpreadTicks(Number(e.target.value))} /></label>
        <label className="space-y-1">Slippage (ticks / side)<input aria-label="Slippage ticks" className="w-full rounded border border-edge bg-ink p-2" type="number" min={0} max={1000} step={0.1} value={slippageTicks} onChange={(e) => setSlippageTicks(Number(e.target.value))} /></label>
        <label className="space-y-1">Commission (ticks / side)<input aria-label="Commission ticks" className="w-full rounded border border-edge bg-ink p-2" type="number" min={0} max={1000} step={0.1} value={commissionTicks} onChange={(e) => setCommissionTicks(Number(e.target.value))} /></label>
        <label className="space-y-1">In-sample (%)<input aria-label="In-sample percent" className="w-full rounded border border-edge bg-ink p-2" type="number" min={20} max={90} value={inSamplePercent} onChange={(e) => setInSamplePercent(Number(e.target.value))} /></label>
        <label className="space-y-1">Validation (%)<input aria-label="Validation percent" className="w-full rounded border border-edge bg-ink p-2" type="number" min={0} max={70} value={validationPercent} onChange={(e) => setValidationPercent(Number(e.target.value))} /></label>
        <p className="col-span-2 text-mute">Out-of-sample: {100 - inSamplePercent - validationPercent}% · only the most recent 5,000 candles are evaluated.</p>
        <button className="col-span-2 rounded bg-edge px-3 py-2 font-medium hover:opacity-80 disabled:opacity-50" type="submit" disabled={loading}>
          {loading ? "Loading history and running…" : `Run ${symbol} ${timeframe} backtest`}
        </button>
      </form>
      {error && <p className="mt-3 text-xs text-liquid" data-testid="backtest-error">Backtest unavailable: {error}</p>}
      {report && <div className="mt-3" data-testid="backtest-results">
        <p className="mb-2 text-xs text-mute">{report.result_persisted && report.backtest_id ? `Saved · ${report.backtest_id}` : "Completed · not saved (configure Supabase to retain this result)."}</p>
        <p className="mb-2 text-xs font-semibold" data-testid="backtest-data-mode">Data: {report.data_mode === "test_mock_endpoint" ? "TEST MOCK DATA" : report.data_source.toUpperCase()} · {report.candles_used} closed candles</p>
        <Metric label="Eligible setups / simulated trades" value={`${report.eligible_setups} / ${report.executed_trades}`} />
        <Metric label="Closed trades (win / loss / even)" value={`${report.closed_trades} (${report.wins} / ${report.losses} / ${report.breakeven_trades})`} />
        <Metric label="Win rate" value={report.win_rate_percent === null ? "n/a" : `${report.win_rate_percent.toFixed(1)}%`} />
        <Metric label="Gross / costs / net" value={`${report.gross_r.toFixed(2)}R / ${report.total_cost_r.toFixed(2)}R / ${report.net_r.toFixed(2)}R`} />
        <Metric label="Average win / loss" value={`${report.average_win_r === null ? "n/a" : `${report.average_win_r.toFixed(2)}R`} / ${report.average_loss_r === null ? "n/a" : `${report.average_loss_r.toFixed(2)}R`}`} />
        <Metric label="Expectancy" value={report.expectancy_r === null ? "n/a" : `${report.expectancy_r.toFixed(2)}R / trade`} />
        <Metric label="Profit factor" value={report.profit_factor === null ? "n/a" : report.profit_factor.toFixed(2)} />
        <Metric label="Maximum drawdown / losing streak" value={`${report.max_drawdown_r.toFixed(2)}R / ${report.longest_losing_streak}`} />
        <Metric label="Average duration" value={report.average_duration_seconds === null ? "n/a" : `${(report.average_duration_seconds / 3600).toFixed(1)} hours`} />
        <EquityPlot report={report} />
        <h4 className="mt-3 text-xs font-semibold">Chronological evaluation periods</h4>
        {report.periods.map((period) => <Metric key={period.name} label={`${period.name.replace("_", " ")} · ${period.start_time ? new Date(period.start_time * 1000).toISOString().slice(0, 10) : "n/a"}–${period.end_time ? new Date(period.end_time * 1000).toISOString().slice(0, 10) : "n/a"} · ${period.candles} candles · ${period.trades} trades`} value={`${period.net_r.toFixed(2)}R · ${period.expectancy_r === null ? "n/a" : `${period.expectancy_r.toFixed(2)}R/trade`}`} />)}
        <h4 className="mt-3 text-xs font-semibold">Performance breakdown</h4>
        {[...report.by_instrument, ...report.by_timeframe, ...report.by_setup_type, ...report.by_direction].map((group, index) => <Metric key={`${group.key}-${index}`} label={`${group.key} · ${group.trades} trades`} value={`${group.net_r.toFixed(2)}R`} />)}
        <details className="mt-3 rounded border border-edge p-2">
          <summary className="cursor-pointer text-xs font-semibold">Trade ledger ({report.trades.length})</summary>
          <div className="mt-2 max-h-64 space-y-2 overflow-y-auto">
            {report.trades.map((trade, index) => <div key={`${trade.entry_time}-${index}`} className="border-b border-edge pb-2 text-xs">
              <p>{new Date(trade.entry_time * 1000).toISOString()} · {trade.direction} {trade.setup_type} · {trade.evaluation_period}</p>
              <p>Entry {trade.entry_price.toFixed(DIGITS[symbol])} · stop {trade.stop_loss.toFixed(DIGITS[symbol])} · target {trade.target_price.toFixed(DIGITS[symbol])}</p>
              <p>{trade.outcome} · gross {trade.gross_result_r?.toFixed(2) ?? "n/a"}R · costs {trade.cost_r.toFixed(2)}R · net {trade.realized_r?.toFixed(2) ?? "open"}R · MFE {trade.max_favourable_r.toFixed(2)}R / MAE {trade.max_adverse_r.toFixed(2)}R</p>
            </div>)}
          </div>
        </details>
        <details className="mt-2 text-xs text-mute"><summary className="cursor-pointer">Metric definitions and assumptions</summary>
          <p className="mt-2">Win rate is wins divided by closed trades; expectancy is average net R; profit factor is positive net R divided by absolute negative net R (undefined when there are no losses); drawdown is the peak-to-trough cumulative net R decline. MAE/MFE are within-candle price excursions in initial-risk units.</p>
          {report.assumptions.map((assumption) => <p className="mt-1" key={assumption}>{assumption}</p>)}
        </details>
        {report.warnings.map((warning, i) => <p key={i} className="mt-2 text-xs text-liquid">{warning}</p>)}
        <p className="mt-2 text-xs text-mute">Data quality: {report.data_quality.gap_count} timestamp gaps; {report.excluded_unclosed_candles} unclosed candles excluded; {report.data_quality.warmup_candles} warm-up candles. This result is not saved after the response. Simulated R only; no account balance or broker orders.</p>
      </div>}
    </details>
  );
}
