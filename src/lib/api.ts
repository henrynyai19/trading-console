import type { AnalysisResponse, BacktestResponse, Candle, SetupResponse, Symbol, Timeframe } from "./types";
// Local development uses its separate FastAPI process. A hosted Vercel deployment
// routes /api and /ws to FastAPI on the same origin, so no public API hostname is needed.
const BASE = process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000");

export type CandleResult =
  | { ok: true; candles: Candle[]; provider: string; providerSymbol: string; source: string; dataMode: string | null }
  | { ok: false; reason: string };

export const wsUrl = (symbol: Symbol, tf: Timeframe) => {
  const base = BASE || `${window.location.protocol}//${window.location.host}`;
  return `${base.replace(/^http/, "ws")}/ws?symbol=${symbol}&tf=${tf}`;
};

export async function fetchCandles(symbol: Symbol, tf: Timeframe, signal?: AbortSignal): Promise<CandleResult> {
  try {
    const r = await fetch(`${BASE}/api/candles?symbol=${symbol}&tf=${tf}`, { signal, cache: "no-store" });
    if (r.ok) {
      const b = await r.json();
      if (!Array.isArray(b.candles)) return { ok: false, reason: "The backend returned malformed candle data." };
      return { ok: true, candles: b.candles, provider: b.provider ?? "", providerSymbol: b.provider_symbol ?? "", source: b.source ?? "", dataMode: b.data_mode ?? null };
    }
    const body = await r.json().catch(() => null);
    return { ok: false, reason: body?.detail?.reason ?? `Backend returned ${r.status}.` };
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return { ok: false, reason: "Cannot reach the analysis backend." };
  }
}

export type AnalysisResult = { ok: true; analysis: AnalysisResponse } | { ok: false; reason: string };

const isAnalysis = (b: unknown): b is AnalysisResponse => {
  const a = b as AnalysisResponse;
  return !!a && typeof a.status === "string" && typeof a.freshness === "object" && Array.isArray(a.swings) &&
    Array.isArray(a.zones) && Array.isArray(a.events) && Array.isArray(a.trendlines);
};

export async function fetchAnalysis(symbol: Symbol, tf: Timeframe, signal?: AbortSignal): Promise<AnalysisResult> {
  try {
    const r = await fetch(`${BASE}/api/analysis/${symbol}?timeframe=${tf}`, { signal, cache: "no-store" });
    const body = await r.json().catch(() => null);
    if (r.ok) return isAnalysis(body) ? { ok: true, analysis: body } : { ok: false, reason: "The backend returned malformed analysis data." };
    return { ok: false, reason: body?.detail?.reason ?? `Analysis request failed (${r.status}).` };
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return { ok: false, reason: "Cannot reach the analysis backend." };
  }
}

export type SetupResult = { ok: true; setups: SetupResponse } | { ok: false; reason: string };

export async function fetchSetups(symbol: Symbol, tf: Timeframe, signal?: AbortSignal): Promise<SetupResult> {
  try {
    const r = await fetch(`${BASE}/api/setups/${symbol}?timeframe=${tf}`, { signal, cache: "no-store" });
    const body = await r.json().catch(() => null);
    if (!r.ok) return { ok: false, reason: body?.detail?.reason ?? `Setup request failed (${r.status}).` };
    if (!body || !Array.isArray(body.setups) || typeof body.status !== "string") {
      return { ok: false, reason: "The backend returned malformed setup data." };
    }
    return { ok: true, setups: body as SetupResponse };
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    return { ok: false, reason: "Cannot reach the setup service." };
  }
}

export async function fetchBacktest(symbol: Symbol, tf: Timeframe, options: { startDate: string; endDate: string; riskReward: number; spreadTicks: number; slippageTicks: number; commissionTicks: number; inSamplePercent: number; validationPercent: number }): Promise<{ ok: true; report: BacktestResponse } | { ok: false; reason: string }> {
  try {
    const startTime = Math.floor(Date.parse(`${options.startDate}T00:00:00Z`) / 1000);
    const endTime = Math.floor(Date.parse(`${options.endDate}T23:59:59Z`) / 1000);
    if (!Number.isFinite(startTime) || !Number.isFinite(endTime) || endTime <= startTime) return { ok: false, reason: "Choose a valid date range." };
    const r = await fetch(`${BASE}/api/backtests`, { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument: symbol, timeframe: tf, start_time: startTime, end_time: endTime,
        risk_reward: options.riskReward, spread_ticks: options.spreadTicks, slippage_ticks: options.slippageTicks,
        commission_ticks_per_side: options.commissionTicks, in_sample_percent: options.inSamplePercent,
        validation_percent: options.validationPercent }) });
    const body = await r.json().catch(() => null);
    if (!r.ok) return { ok: false, reason: typeof body?.detail === "string" ? body.detail : body?.detail?.reason ?? `Backtest request failed (${r.status}).` };
    if (!body || !Array.isArray(body.trades) || typeof body.closed_trades !== "number") return { ok: false, reason: "The backend returned malformed backtest data." };
    return { ok: true, report: body as BacktestResponse };
  } catch {
    return { ok: false, reason: "Cannot reach the backtest service." };
  }
}
