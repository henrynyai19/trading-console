import type { AnalysisResponse, Candle, Symbol, Timeframe } from "./types";
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type CandleResult =
  | { ok: true; candles: Candle[]; providerSymbol: string; source: string }
  | { ok: false; reason: string };

export const wsUrl = (symbol: Symbol, tf: Timeframe) => `${BASE.replace(/^http/, "ws")}/ws?symbol=${symbol}&tf=${tf}`;

export async function fetchCandles(symbol: Symbol, tf: Timeframe, signal?: AbortSignal): Promise<CandleResult> {
  try {
    const r = await fetch(`${BASE}/api/candles?symbol=${symbol}&tf=${tf}`, { signal, cache: "no-store" });
    if (r.ok) {
      const b = await r.json();
      if (!Array.isArray(b.candles)) return { ok: false, reason: "The backend returned malformed candle data." };
      return { ok: true, candles: b.candles, providerSymbol: b.provider_symbol ?? "", source: b.source ?? "" };
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
