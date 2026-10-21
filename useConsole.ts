import { create } from "zustand";
import { mergeCandles } from "@/lib/candles";
import type { AnalysisResponse, Candle, ConnectionStatus, FeedState, Layers, ServerMessage, Symbol, Timeframe } from "@/lib/types";

export const keyOf = (s: Symbol, t: Timeframe) => `${s}|${t}`;

/** Time of the latest official closed candle. Changes only when a candle completes, never on a tick. */
export const lastClosedTime = (c: Candle[]): number => {
  for (let i = c.length - 1; i >= 0; i--) if (c[i].complete && !c[i].provisional) return c[i].time;
  return 0;
};

interface ConsoleState {
  symbol: Symbol; timeframe: Timeframe;
  status: ConnectionStatus; reason: string;
  candles: Candle[];
  epoch: number; // bumps whenever the chart must reload all data instead of updating the last candle
  providerSymbol: string;
  feed: FeedState; marketOpen: boolean | null; serverStale: boolean;
  lastMessageAt: number; lastCandleAt: number;
  analysis: AnalysisResponse | null; analysisState: "idle" | "loading" | "ready" | "error"; analysisError: string;
  layers: Layers;
  setAnalysisLoading: (key: string) => void;
  setAnalysis: (key: string, a: AnalysisResponse) => void;
  setAnalysisError: (key: string, reason: string) => void;
  toggleLayer: (name: keyof Layers) => void;
  setSymbol: (s: Symbol) => void; setTimeframe: (t: Timeframe) => void;
  setHistory: (key: string, c: Candle[], providerSymbol: string) => void;
  setUnavailable: (key: string, reason: string) => void;
  setFeedState: (key: string, s: FeedState) => void;
  applyMessage: (key: string, m: ServerMessage) => void;
}

const cleared = (epoch: number) => ({
  candles: [] as Candle[], status: "connecting" as ConnectionStatus, reason: "", providerSymbol: "",
  feed: "idle" as FeedState, marketOpen: null, serverStale: false, lastMessageAt: 0, lastCandleAt: 0, epoch: epoch + 1,
  analysis: null as AnalysisResponse | null, analysisState: "idle" as "idle" | "loading" | "ready" | "error", analysisError: "",
});

export const useConsole = create<ConsoleState>((set, get) => {
  const current = (key: string) => key === keyOf(get().symbol, get().timeframe);
  return {
    symbol: "XAUUSD", timeframe: "15m", ...cleared(0),
    layers: { zones: true, swings: true, structure: true, trendlines: true },
    // Analysis is tied to one instrument/timeframe; responses for any other selection are discarded.
    setAnalysisLoading: (key) => { if (current(key)) set((s) => ({ analysisState: s.analysis ? "ready" : "loading" })); },
    setAnalysis: (key, analysis) => {
      if (current(key) && analysis.instrument === get().symbol && analysis.timeframe === get().timeframe) set({ analysis, analysisState: "ready", analysisError: "" });
    },
    setAnalysisError: (key, reason) => { if (current(key)) set({ analysis: null, analysisState: "error", analysisError: reason }); },
    toggleLayer: (name) => set((s) => ({ layers: { ...s.layers, [name]: !s.layers[name] } })),
    // Switching clears data immediately so two instruments/timeframes are never drawn together.
    setSymbol: (symbol) => set((s) => ({ symbol, ...cleared(s.epoch) })),
    setTimeframe: (timeframe) => set((s) => ({ timeframe, ...cleared(s.epoch) })),
    setHistory: (key, candles, providerSymbol) => {
      if (!current(key)) return; // response for a previous selection
      set((s) => ({ candles: mergeCandles([], candles), providerSymbol, status: "live", reason: "", epoch: s.epoch + 1, lastMessageAt: Date.now() }));
    },
    setUnavailable: (key, reason) => {
      if (!current(key)) return;
      set((s) => ({ candles: [], status: "unavailable", reason, feed: "closed", epoch: s.epoch + 1 }));
    },
    setFeedState: (key, feed) => { if (current(key)) set({ feed }); },
    applyMessage: (key, m) => {
      if (!current(key)) return;
      const now = Date.now();
      switch (m.type) {
        case "status":
          set({ marketOpen: m.market_open, serverStale: m.stale, lastMessageAt: now, providerSymbol: m.provider_symbol || get().providerSymbol });
          break;
        case "backfill":
          set((s) => ({ candles: mergeCandles(s.candles, m.candles), epoch: s.epoch + 1, lastMessageAt: now }));
          break;
        case "reconcile":
          set((s) => ({ candles: mergeCandles(s.candles, m.candles, m.drop), epoch: s.epoch + 1, lastMessageAt: now }));
          break;
        case "candle":
          set((s) => {
            const last = s.candles.at(-1);
            const older = last !== undefined && m.candle.time < last.time; // would break series.update()
            return { candles: mergeCandles(s.candles, [m.candle]), epoch: older ? s.epoch + 1 : s.epoch, lastMessageAt: now, lastCandleAt: now };
          });
          break;
        case "error":
          get().setUnavailable(key, m.reason);
          break;
      }
    },
  };
});
