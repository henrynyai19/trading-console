export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export type SignalHorizon = "short_term" | "long_term";
export const SIGNAL_HORIZON_TIMEFRAME: Record<SignalHorizon, Timeframe> = { short_term: "15m", long_term: "4h" };
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
export interface SetupTarget { r_multiple: number; price: number }
export interface TradeSetup {
  id: string; instrument: string; provider_symbol: string; timeframe: string;
  setup_type: "support_retest" | "resistance_retest" | "breakout_retest" | "trend_continuation";
  direction: "long" | "short"; status: "developing" | "awaiting_confirmation" | "confirmed" | "active_reference" | "entry_touched" | "target_1_reached" | "target_2_reached" | "target_3_reached" | "stopped_out" | "invalidated" | "expired" | "active" | "target_touched";
  quality_score: number; score_explanation: string[]; caution_reasons: string[]; created_at: number; detected_at: number;
  confirmed_at: number | null; data_time: number; data_freshness: "fresh" | "market_closed" | "stale" | "unknown"; expires_at: number;
  reference_level: number;
  invalidation_price: number; entry_lower: number; entry_upper: number; planned_entry: number;
  stop_loss: number; targets: SetupTarget[]; risk_distance: number; risk_currency: string | null; monetary_risk_available: boolean;
  source_zone_id: string | null; source_event_id: string | null; higher_timeframes: { timeframe: string; direction: string }[];
  higher_timeframe_direction: string; lower_timeframe_confirmation: boolean; mtf_alignment: string; mtf_conflicts: string[];
  opposing_zone_type: string | null; opposing_zone_price: number | null; opposing_zone_strength: number | null;
  confirmation_explanation: string; invalidation_explanation: string; explanation: string;
  last_processed_time: number | null; confirmed_snapshot: Record<string, unknown> | null;
  lifecycle_events: { status: string; at: number; candle_time: number | null; reason: string }[];
}
export interface SetupResponse {
  instrument: string; provider_symbol: string; timeframe: string;
  status: "ok" | "insufficient_data" | "stale"; reason: string | null; analysis_time: number;
  data_time: number | null; data_freshness: "fresh" | "market_closed" | "stale" | "unknown";
  current_price: number | null; warnings: string[]; setups: TradeSetup[]; history_persistent: boolean;
}
export interface BacktestTrade {
  instrument: string; timeframe: string; direction: "long" | "short"; setup_type: "breakout" | "reversal";
  signal_time: number; confirmation_time: number | null; entry_time: number;
  exit_time: number | null; entry_price: number; stop_loss: number; target_price: number; exit_price: number | null;
  outcome: "target" | "stop" | "open"; realized_r: number | null;
  gross_result_r: number | null; cost_r: number; max_favourable_r: number; max_adverse_r: number;
  duration_seconds: number | null; evaluation_period: "in_sample" | "validation" | "out_of_sample"; lifecycle_events: string[];
}
export interface BacktestEquityPoint { time: number; equity_r: number; drawdown_r: number }
export interface BacktestGroup { key: string; trades: number; net_r: number; expectancy_r: number | null }
export interface BacktestPeriod { name: "in_sample" | "validation" | "out_of_sample"; start_time: number | null; end_time: number | null; candles: number; trades: number; net_r: number; expectancy_r: number | null }
export interface BacktestResponse {
  backtest_id: string | null;
  instrument: string; provider_symbol: string; timeframe: string; data_source: string;
  data_mode: "configured_provider" | "test_mock_endpoint"; strategy: "structure_breaks"; strategy_version: string; strategy_configuration: Record<string, number | string | boolean>;
  candle_fingerprint: string; candles_used: number; eligible_setups: number; executed_trades: number; result_persisted: boolean; risk_reward: 1.5 | 2 | 3;
  spread_ticks: number; slippage_ticks: number; commission_ticks_per_side: number; closed_trades: number; wins: number; losses: number;
  win_rate_percent: number | null; net_r: number; expectancy_r: number | null; profit_factor: number | null;
  max_drawdown_r: number; trades: BacktestTrade[]; warnings: string[]; requested_start: number | null; requested_end: number | null;
  data_quality: { valid_closed_candles: number; warmup_candles: number; gap_count: number; gap_durations_seconds: number[]; timestamps_utc: boolean };
  excluded_unclosed_candles: number;
  gross_r: number; total_cost_r: number; average_win_r: number | null; average_loss_r: number | null;
  breakeven_trades: number; longest_losing_streak: number; average_duration_seconds: number | null;
  equity_curve: BacktestEquityPoint[]; drawdown_curve: BacktestEquityPoint[]; over_time: BacktestEquityPoint[];
  by_instrument: BacktestGroup[]; by_timeframe: BacktestGroup[]; by_setup_type: BacktestGroup[]; by_direction: BacktestGroup[];
  periods: BacktestPeriod[]; assumptions: string[];
}
export interface Layers { zones: boolean; swings: boolean; structure: boolean; trendlines: boolean }
export const LAYER_LABEL: Record<keyof Layers, string> = { zones: "Verified support / resistance", swings: "Swing labels (HH, HL, LH, LL)", structure: "BOS / CHoCH", trendlines: "Trendlines" };
