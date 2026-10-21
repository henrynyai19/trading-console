export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export const TF_LABEL: Record<Timeframe, string> = { "1m": "1M", "5m": "5M", "15m": "15M", "30m": "30M", "1h": "1H", "4h": "4H", "1d": "D", "1w": "W" };
export const SYMBOLS = ["XAUUSD", "USDJPY", "NASDAQ", "US30", "EURUSD", "GBPUSD"] as const;
export type Symbol = (typeof SYMBOLS)[number];
export const DIGITS: Record<Symbol, number> = { XAUUSD: 2, USDJPY: 3, NASDAQ: 1, US30: 1, EURUSD: 5, GBPUSD: 5 };
export type ConnectionStatus = "connecting" | "live" | "unavailable";
export type FeedState = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export interface Candle {
  time: number; open: number; high: number; low: number; close: number; volume: number;
  complete: boolean;
  provisional?: boolean; // built from sampled stream prices, not yet confirmed by an official candle
}

export type ServerMessage =
  | { type: "backfill"; symbol: string; timeframe: string; candles: Candle[] }
  | { type: "candle"; symbol: string; timeframe: string; candle: Candle }
  | { type: "reconcile"; symbol: string; timeframe: string; candles: Candle[]; drop: number[] }
  | { type: "status"; symbol: string; provider_symbol: string; market_open: boolean | null; upstream: string; stale: boolean; last_price_at: number | null; server_time: number }
  | { type: "error"; status: "unavailable"; reason: string };

// ---- Phase 3: analysis ----
export type TrendClass = "Strong Bullish" | "Bullish" | "Sideways" | "Bearish" | "Strong Bearish" | "Transitional";
export type StructState = "bullish" | "bearish" | "undetermined";
export interface SwingPoint { id: string; kind: "high" | "low"; label: "HH" | "LH" | "EQH" | "HL" | "LL" | "EQL" | null; price: number; time: number; confirmed_at: number; timeframe: string; strength: number }
export interface Zone {
  id: string; instrument: string; timeframe: string; type: "support" | "resistance"; origin_type: "support" | "resistance";
  upper: number; lower: number; strength: number; touches: number; start_time: number; created_at: number; last_interaction: number;
  status: "active" | "invalidated"; role_reversed: boolean; role_reversal_time: number | null;
}
export interface StructureEvent { id: string; kind: "BOS" | "CHoCH"; direction: "bullish" | "bearish"; level: number; broken_swing_time: number; swing_confirmed_at: number; break_time: number; confirmed_at: number; instrument: string; timeframe: string }
export interface Trendline {
  id: string; timeframe: string; direction: "bullish" | "bearish"; anchors: { time: number; price: number }[];
  start_time: number; start_price: number; end_time: number; end_price: number; slope_per_bar: number; touches: number;
  strength: number; status: "active" | "broken"; break_time: number | null; created_at: number;
}
export interface TrendComponent { name: string; points: number; max_points: number; detail: string }
export interface TrendInfo { classification: TrendClass; score: number; components: TrendComponent[]; explanation: string[]; structure_state: StructState; bars_since_last_event: number | null }
export interface TFTrend { timeframe: string; classification: TrendClass | null; direction: "bullish" | "bearish" | "neutral" | "unavailable"; score: number | null; structure_state: StructState; reason: string | null }
export interface MTF { primary: TFTrend; higher: TFTrend[]; alignment: "aligned" | "partial" | "conflicting" | "neutral" | "unavailable"; structural_alignment: string; bias: string; conflicts: string[]; summary: string }
export interface Freshness { status: "fresh" | "stale" | "market_closed" | "unknown"; last_closed_time: number | null; last_close_at: number | null; age_seconds: number | null; trading_age_seconds: number | null }
export interface AnalysisResponse {
  instrument: string; provider_symbol: string; timeframe: string; analysis_time: number;
  status: "ok" | "insufficient_data" | "stale"; reason: string | null; freshness: Freshness; candles_used: number; warnings: string[];
  trend: TrendInfo | null; swings: SwingPoint[]; zones: Zone[]; nearest_support: string | null; nearest_resistance: string | null;
  events: StructureEvent[]; trendlines: Trendline[]; mtf: MTF | null; commentary: string[];
}
export interface Layers { zones: boolean; swings: boolean; structure: boolean; trendlines: boolean }
export const LAYER_LABEL: Record<keyof Layers, string> = { zones: "Support / resistance zones", swings: "Swing labels (HH, HL, LH, LL)", structure: "BOS / CHoCH", trendlines: "Trendlines" };
