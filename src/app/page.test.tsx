import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({
  setData: vi.fn(), update: vi.fn(), setMarkers: vi.fn(), attachPrimitive: vi.fn(), detachPrimitive: vi.fn(),
  removeSeries: vi.fn(), addLineSeries: vi.fn(), lines: [] as { setData: ReturnType<typeof vi.fn> }[],
}));
vi.mock("lightweight-charts", () => ({
  createChart: () => ({
    addCandlestickSeries: () => ({ setData: m.setData, update: m.update, setMarkers: m.setMarkers, attachPrimitive: m.attachPrimitive, detachPrimitive: m.detachPrimitive }),
    addLineSeries: (o: unknown) => { m.addLineSeries(o); const l = { setData: vi.fn() }; m.lines.push(l); return l; },
    removeSeries: m.removeSeries,
    timeScale: () => ({ fitContent: vi.fn() }),
    remove: vi.fn(),
  }),
}));
const { setData, update } = m;

import Page from "./page";
import { useConsole } from "@/store/useConsole";

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeWS.instances.push(this); }
  close() { this.closed = true; this.onclose?.(); }
  open() { act(() => this.onopen?.()); }
  send(o: unknown) { act(() => this.onmessage?.({ data: JSON.stringify(o) })); }
  drop() { act(() => this.onclose?.()); }
}

const bar = (time: number, over = {}) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 1, complete: true, provisional: false, ...over });
const ok = (candles: unknown[], providerSymbol = "XAU_USD") =>
  Promise.resolve(new Response(JSON.stringify({ candles, provider_symbol: providerSymbol, source: "oanda" }), { status: 200 }));
const unavailable = () =>
  Promise.resolve(new Response(JSON.stringify({ detail: { status: "unavailable", reason: "No market-data provider is configured." } }), { status: 503 }));
const status = (over = {}) => ({ type: "status", symbol: "XAUUSD", provider_symbol: "XAU_USD", market_open: true, upstream: "connected", stale: false, last_price_at: 1, server_time: 1, ...over });

beforeEach(() => {
  Object.values(m).forEach((f) => typeof f === "function" && (f as ReturnType<typeof vi.fn>).mockClear()); m.lines.length = 0;
  setData.mockClear(); update.mockClear(); FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  useConsole.getState().setSymbol("XAUUSD");
  useConsole.getState().setTimeframe("15m");
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("console", () => {
  it("shows DATA UNAVAILABLE, disables signals and opens no stream when the backend has no data", async () => {
    vi.stubGlobal("fetch", vi.fn(unavailable));
    render(<Page />);
    expect(screen.getByText("Connecting to market data")).toBeTruthy();
    expect(await screen.findByText("DATA UNAVAILABLE")).toBeTruthy();
    expect(screen.getByText(/No market-data provider is configured/)).toBeTruthy();
    expect(screen.getByText(/Signals are disabled while market data is unavailable/)).toBeTruthy();
    expect(screen.getByTestId("connection").textContent).toContain("Disconnected");
    expect(FakeWS.instances).toHaveLength(0);
  });

  it("reports an unreachable backend as unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("fail"))));
    render(<Page />);
    expect(await screen.findByText(/Cannot reach the analysis backend/)).toBeTruthy();
  });

  it("loads history, shows the provider instrument and live status, then updates the developing candle", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok([bar(100), bar(160, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    expect(FakeWS.instances[0].url).toContain("/ws?symbol=XAUUSD&tf=15m");
    expect(setData).toHaveBeenLastCalledWith([expect.objectContaining({ time: 100 }), expect.objectContaining({ time: 160 })]);
    expect(screen.getByTestId("provider-label").textContent).toBe("MARKET · XAU_USD");

    FakeWS.instances[0].open();
    FakeWS.instances[0].send(status());
    expect(screen.getByTestId("connection").textContent).toContain("Live");
    expect(screen.getByTestId("market").textContent).toBe("Market open");

    setData.mockClear();
    FakeWS.instances[0].send({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(160, { complete: false, close: 1.8 }) });
    expect(update).toHaveBeenLastCalledWith({ time: 160, open: 1, high: 2, low: 0.5, close: 1.8 });
    expect(setData).not.toHaveBeenCalled(); // an in-place update, not a reload
    expect(screen.getByTestId("price").textContent).toBe("1.80");

    FakeWS.instances[0].send({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(1060, { complete: false, close: 1.9 }) });
    expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ time: 1060 })); // new candle appends
  });

  it("clearly labels synthetic preview data instead of reporting it as live", async () => {
    vi.stubEnv("NEXT_PUBLIC_SYNTHETIC_PREVIEW", "true");
    vi.stubGlobal("fetch", vi.fn(() => ok([bar(100), bar(160, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    FakeWS.instances[0].send(status());
    expect(screen.getByTestId("synthetic-preview-banner").textContent).toContain("not live quotes");
    expect(screen.getByTestId("provider-label").textContent).toContain("SYNTHETIC PREVIEW");
    expect(screen.getByTestId("connection").textContent).toContain("Synthetic stream");
    expect(screen.getByTestId("market").textContent).toBe("Synthetic session");
    expect(screen.getByTestId("freshness").textContent).toBe("Sample prices");
  });

  it("applies reconcile messages by reloading the series and dropping provisional candles", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok([bar(100, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    FakeWS.instances[0].send({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(130, { complete: false, provisional: true }) });
    setData.mockClear();
    FakeWS.instances[0].send({ type: "reconcile", symbol: "XAUUSD", timeframe: "15m", drop: [130], candles: [bar(100), bar(1000, { complete: false })] });
    expect(setData).toHaveBeenLastCalledWith([expect.objectContaining({ time: 100, close: 1.5 }), expect.objectContaining({ time: 1000 })]);
  });

  it("flags stale data and shows Reconnecting when the socket drops, then recovers", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok([bar(100, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    FakeWS.instances[0].send(status());
    expect(screen.getByTestId("freshness").textContent).not.toBe("Stale data");
    FakeWS.instances[0].drop();
    expect(screen.getByTestId("connection").textContent).toContain("Reconnecting");
    expect(screen.getByTestId("freshness").textContent).toBe("Stale data");
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2), { timeout: 3000 });
    FakeWS.instances[1].open();
    FakeWS.instances[1].send(status());
    expect(screen.getByTestId("connection").textContent).toContain("Live");
  });

  it("shows market closed from the server status", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok([bar(100, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    FakeWS.instances[0].send(status({ market_open: false }));
    expect(screen.getByTestId("freshness").textContent).toBe("Market closed");
  });

  it("switching instrument closes the old stream, requests the new symbol and never mixes data", async () => {
    const f = vi.fn((url: string) => ok([bar(url.includes("USDJPY") ? 500 : 100)], url.includes("USDJPY") ? "USD_JPY" : "XAU_USD"));
    vi.stubGlobal("fetch", f);
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "USDJPY" } });
    expect(FakeWS.instances[0].closed).toBe(true);
    expect(setData).toHaveBeenLastCalledWith([]); // cleared immediately
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    expect(FakeWS.instances[1].url).toContain("symbol=USDJPY&tf=15m");
    expect(setData).toHaveBeenLastCalledWith([expect.objectContaining({ time: 500 })]);
    expect(screen.getByTestId("provider-label").textContent).toBe("MARKET · USD_JPY");
  });

  it("switching timeframe requests the new timeframe and marks it pressed", async () => {
    const f = vi.fn(() => ok([bar(100)]));
    vi.stubGlobal("fetch", f);
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "4H" }));
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    expect(FakeWS.instances[1].url).toContain("tf=4h");
    expect(screen.getByRole("button", { name: "4H" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "15M" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("a slow response for a previous selection cannot overwrite the new one", async () => {
    let resolveOld: (r: Response) => void = () => {};
    const f = vi.fn((url: string) =>
      url.includes("XAUUSD") ? new Promise<Response>((res) => { resolveOld = res; }) : ok([bar(500)], "USD_JPY"));
    vi.stubGlobal("fetch", f);
    render(<Page />);
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "USDJPY" } });
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    await act(async () => { resolveOld(new Response(JSON.stringify({ candles: [bar(100)], provider_symbol: "XAU_USD" }), { status: 200 })); });
    expect(setData).not.toHaveBeenCalledWith([expect.objectContaining({ time: 100 })]);
    expect(screen.getByTestId("provider-label").textContent).toBe("MARKET · USD_JPY");
    expect(FakeWS.instances).toHaveLength(1);
  });

  it("ignores late socket messages that belong to a previous selection", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => ok([bar(url.includes("USDJPY") ? 500 : 100, { complete: false })])));
    render(<Page />);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    const old = FakeWS.instances[0];
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "USDJPY" } });
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    update.mockClear();
    old.onmessage?.({ data: JSON.stringify({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(100, { complete: false, close: 99 }) }) });
    expect(update).not.toHaveBeenCalled();
  });
});

// ---------------- Phase 3: analysis ----------------
import type { AnalysisResponse, TFTrend } from "@/lib/types";

const tfTrend = (timeframe: string, classification: string | null, direction: string) =>
  ({ timeframe, classification, direction, score: 50, structure_state: "bullish", reason: null }) as TFTrend;
const analysisFor = (over: Partial<AnalysisResponse> = {}): AnalysisResponse => ({
  instrument: "XAUUSD", provider_symbol: "XAU_USD", timeframe: "15m", analysis_time: 1, status: "ok", reason: null,
  freshness: { status: "fresh", last_closed_time: 100, last_close_at: 1000, age_seconds: 5, trading_age_seconds: 5 },
  candles_used: 200, warnings: [],
  trend: { classification: "Bullish", score: 62, components: [], explanation: ["Swing structure: +40/40 (HH, HL)", "Classified Bullish: structure, events and averages agree."], structure_state: "bullish", bars_since_last_event: 4 },
  swings: [
    { id: "high-100", kind: "high", label: "HH", price: 2, time: 100, confirmed_at: 1000, timeframe: "15m", strength: 50 },
    { id: "low-130", kind: "low", label: "HL", price: 0.6, time: 130, confirmed_at: 1030, timeframe: "15m", strength: 40 }],
  zones: [
    { id: "z1", instrument: "XAUUSD", timeframe: "15m", type: "support", origin_type: "support", upper: 1.2, lower: 1.0, strength: 70, touches: 3, start_time: 100, created_at: 1000, last_interaction: 130, status: "active", role_reversed: false, role_reversal_time: null },
    { id: "z2", instrument: "XAUUSD", timeframe: "15m", type: "resistance", origin_type: "resistance", upper: 2.2, lower: 2.0, strength: 55, touches: 2, start_time: 100, created_at: 1000, last_interaction: 100, status: "active", role_reversed: false, role_reversal_time: null }],
  nearest_support: "z1", nearest_resistance: "z2",
  events: [{ id: "BOS-bullish-160", kind: "BOS", direction: "bullish", level: 1.9, broken_swing_time: 100, swing_confirmed_at: 140, break_time: 160, confirmed_at: 1060, instrument: "XAUUSD", timeframe: "15m" }],
  trendlines: [{ id: "tl1", timeframe: "15m", direction: "bullish", anchors: [{ time: 100, price: 1 }, { time: 130, price: 1.2 }], start_time: 100, start_price: 1, end_time: 160, end_price: 1.4, slope_per_bar: 0.01, touches: 2, strength: 60, status: "active", break_time: null, created_at: 1030 }],
  mtf: { primary: tfTrend("15m", "Bullish", "bullish"), higher: [tfTrend("1h", "Bullish", "bullish"), tfTrend("4h", "Transitional", "neutral")], alignment: "partial", structural_alignment: "partial", bias: "Leaning Bullish", conflicts: ["4H is transitional"], summary: "15M bullish, 1H bullish, 4H transitional. Alignment: partial; overall bias: leaning bullish." },
  commentary: ["15M structure is bullish (trend score +62/100, a rule-based score and not a probability)."],
  ...over,
});
const json = (b: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(b), { status }));
const history = (extra: unknown[] = []) => json({ candles: [bar(100), bar(160, { complete: false }), ...extra], provider_symbol: "XAU_USD", source: "oanda" });
const noSetups = () => json({ instrument: "XAUUSD", provider_symbol: "XAU_USD", timeframe: "15m", status: "ok", reason: null, analysis_time: 1, current_price: 1.1, setups: [], history_persistent: false });
const analysisUrls = (f: ReturnType<typeof vi.fn>) => f.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/api/analysis"));

function router(analysis: (url: string) => Promise<Response> = () => json(analysisFor())) {
  return vi.fn((url: string) => (url.includes("/api/analysis") ? analysis(url) : url.includes("/api/setups/") ? noSetups() : history()));
}

describe("analysis panel and annotations", () => {
  it("fetches analysis for the selected instrument/timeframe and renders structure, MTF, zones and commentary", async () => {
    const f = router();
    vi.stubGlobal("fetch", f);
    render(<Page />);
    expect((await screen.findByTestId("trend-class")).textContent).toBe("Bullish");
    expect(analysisUrls(f)).toEqual([expect.stringContaining("/api/analysis/XAUUSD?timeframe=15m")]);
    expect(screen.getByTestId("trend-score").textContent).toContain("+62 / 100 (rule-based)");
    expect(screen.getByTestId("latest-event").textContent).toBe("bullish BOS @ 1.90");
    expect(screen.getByTestId("nearest-support").textContent).toBe("1.00 - 1.20 (strength 70)");
    expect(screen.getByTestId("nearest-resistance").textContent).toBe("2.00 - 2.20 (strength 55)");
    expect(screen.getByTestId("mtf-1h").textContent).toBe("Bullish");
    expect(screen.getByTestId("mtf-4h").textContent).toBe("Transitional");
    expect(screen.getByTestId("mtf-alignment").textContent).toBe("partial");
    expect(screen.getByTestId("mtf-bias").textContent).toBe("Leaning Bullish");
    expect(screen.getByTestId("mtf-conflicts").textContent).toContain("4H is transitional");
    expect(screen.getByTestId("commentary").textContent).toContain("not a probability");
    expect((await screen.findByTestId("setup-empty")).textContent).toContain("No valid setup");
  });

  it("renders an explainable, risk-defined analytical setup without implying execution", async () => {
    const f = router();
    f.mockImplementation((url: string) => {
      if (url.includes("/api/analysis")) return json(analysisFor());
      if (url.includes("/api/setups/")) return json({
        instrument: "XAUUSD", provider_symbol: "XAU_USD", timeframe: "15m", status: "ok", reason: null,
        analysis_time: 1, data_time: 1, data_freshness: "fresh", warnings: [], current_price: 1.1, history_persistent: true, setups: [{
          id: "s1", instrument: "XAUUSD", provider_symbol: "XAU_USD", timeframe: "15m", setup_type: "trend_continuation",
          direction: "long", status: "entry_touched", quality_score: 78, score_explanation: ["Primary trend agrees"],
          created_at: 1, detected_at: 1, confirmed_at: 1, data_time: 1, data_freshness: "fresh", expires_at: 2,
          reference_level: 1, invalidation_price: 0.9, entry_lower: 1, entry_upper: 1.2,
          planned_entry: 1.1, stop_loss: 0.9, targets: [{ r_multiple: 1.5, price: 1.4 }, { r_multiple: 2, price: 1.5 }, { r_multiple: 3, price: 1.7 }],
          risk_distance: 0.2, risk_currency: null, monetary_risk_available: false, source_zone_id: "z1", source_event_id: null,
          caution_reasons: [], higher_timeframes: [], higher_timeframe_direction: "1h: bullish", lower_timeframe_confirmation: true,
          mtf_alignment: "aligned", mtf_conflicts: [], opposing_zone_type: null, opposing_zone_price: null, opposing_zone_strength: null,
          confirmation_explanation: "Closed candle confirmed the setup.", invalidation_explanation: "Invalid below stop.",
          last_processed_time: 1, confirmed_snapshot: {}, lifecycle_events: [],
          explanation: "Analytical only; no order is created.",
        }],
      });
      return history();
    });
    vi.stubGlobal("fetch", f);
    render(<Page />);
    expect(await screen.findByTestId("trade-setup")).toBeTruthy();
    expect(screen.getByTestId("setup-score").textContent).toContain("78 / 100");
    expect(screen.getByTestId("setup-entry").textContent).toContain("1.00 – 1.20");
    expect(screen.getByTestId("setup-targets").textContent).toContain("3R target");
    expect(screen.getByText(/No broker order or fill is recorded/)).toBeTruthy();
  });

  it("runs a historical simulation with explicit costs and labels test data", async () => {
    const f = vi.fn((url: string, init?: RequestInit) => {
      void init;
      if (url.includes("/api/analysis")) return json(analysisFor());
      if (url.includes("/api/setups/")) return noSetups();
      if (url.includes("/api/backtests")) return json({
        instrument: "XAUUSD", provider_symbol: "XAU_USD", timeframe: "15m", data_source: "oanda", data_mode: "test_mock_endpoint",
        backtest_id: null, strategy: "structure_breaks", strategy_version: "structure-breaks-v1", strategy_configuration: {}, candle_fingerprint: "abc",
        candles_used: 1000, eligible_setups: 3, executed_trades: 3, result_persisted: false, risk_reward: 2, spread_ticks: 1, slippage_ticks: 0.5, closed_trades: 3, wins: 2,
        commission_ticks_per_side: 0,
        losses: 1, win_rate_percent: 66.7, net_r: 2, gross_r: 2.2, total_cost_r: 0.2, average_win_r: 1, average_loss_r: 1,
        expectancy_r: 0.67, profit_factor: 2, max_drawdown_r: 1, breakeven_trades: 0, longest_losing_streak: 1,
        average_duration_seconds: 100, requested_start: 1, requested_end: 1000,
        data_quality: { valid_closed_candles: 1000, warmup_candles: 100, gap_count: 0, gap_durations_seconds: [], timestamps_utc: true }, excluded_unclosed_candles: 1,
        equity_curve: [{ time: 200, equity_r: 2, drawdown_r: 0 }, { time: 300, equity_r: 1, drawdown_r: 1 }],
        drawdown_curve: [], over_time: [], by_instrument: [], by_timeframe: [], by_setup_type: [{ key: "breakout", trades: 3, net_r: 2, expectancy_r: 0.67 }],
        by_direction: [], periods: [{ name: "in_sample", start_time: 1, end_time: 400, candles: 600, trades: 2, net_r: 2, expectancy_r: 1 },
          { name: "validation", start_time: 401, end_time: 700, candles: 200, trades: 1, net_r: 0, expectancy_r: 0 },
          { name: "out_of_sample", start_time: 701, end_time: 1000, candles: 200, trades: 0, net_r: 0, expectancy_r: null }], assumptions: [],
        trades: [{ instrument: "XAUUSD", timeframe: "15m", direction: "long", setup_type: "breakout", signal_time: 100, confirmation_time: 100,
          entry_time: 100, exit_time: 200, entry_price: 2000, stop_loss: 1990, target_price: 2020, exit_price: 2020,
          outcome: "target", realized_r: 2, gross_result_r: 2.1, cost_r: 0.1, max_favourable_r: 2.2, max_adverse_r: 0.4,
          duration_seconds: 100, evaluation_period: "in_sample", lifecycle_events: [] }], warnings: [],
      });
      return history();
    });
    vi.stubGlobal("fetch", f);
    render(<Page />);
    fireEvent.click(await screen.findByText("Historical backtest"));
    fireEvent.click(await screen.findByRole("button", { name: "Run XAUUSD 15m backtest" }));
    expect(await screen.findByTestId("backtest-results")).toBeTruthy();
    expect(screen.getByTestId("backtest-data-mode").textContent).toContain("TEST MOCK DATA");
    expect(screen.getByTestId("backtest-results").textContent).toContain("0.67R / trade");
    expect(screen.getByLabelText("Start date")).toBeTruthy();
    expect(screen.getByText(/out-of-sample/i)).toBeTruthy();
    expect(screen.getByRole("img", { name: /cumulative equity and drawdown/i })).toBeTruthy();
    expect(screen.getByText("Trade ledger (1)")).toBeTruthy();
    const call = f.mock.calls.find((item) => String(item[0]).includes("/api/backtests"));
    expect(call?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ instrument: "XAUUSD", timeframe: "15m", spread_ticks: 0, slippage_ticks: 0 });
  });

  it("draws markers, break levels, trendlines and zones from the analysis, in time order", async () => {
    vi.stubGlobal("fetch", router());
    render(<Page />);
    await screen.findByTestId("trend-class");
    const markers = m.setMarkers.mock.calls.at(-1)![0] as { time: number; text: string }[];
    expect(markers.map((x) => x.text)).toEqual(["HH", "HL", "BOS"]);
    expect(markers.map((x) => x.time)).toEqual([100, 130, 160]);
    expect(m.lines).toHaveLength(2);                                                   // BOS level + trendline
    expect(m.lines[0].setData).toHaveBeenLastCalledWith([{ time: 100, value: 1.9 }, { time: 160, value: 1.9 }]);
    expect(m.lines[1].setData).toHaveBeenLastCalledWith([{ time: 100, value: 1 }, { time: 160, value: 1.4 }]);
    expect(m.attachPrimitive).toHaveBeenCalledTimes(1);
  });

  it("does not redraw annotations or refetch analysis on live ticks", async () => {
    const f = router();
    vi.stubGlobal("fetch", f);
    render(<Page />);
    await screen.findByTestId("trend-class");
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    const draws = { markers: m.setMarkers.mock.calls.length, lines: m.addLineSeries.mock.calls.length, removed: m.removeSeries.mock.calls.length };
    for (let i = 0; i < 6; i++) {
      FakeWS.instances[0].send({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(160, { complete: false, close: 1.5 + i / 100 }) });
    }
    await new Promise((r) => setTimeout(r, 400));
    expect(update).toHaveBeenCalledTimes(6);
    expect(m.setMarkers.mock.calls.length).toBe(draws.markers);
    expect(m.addLineSeries.mock.calls.length).toBe(draws.lines);
    expect(m.removeSeries.mock.calls.length).toBe(draws.removed);
    expect(analysisUrls(f)).toHaveLength(1);
  });

  it("refetches analysis when a candle completes", async () => {
    const f = router();
    vi.stubGlobal("fetch", f);
    render(<Page />);
    await screen.findByTestId("trend-class");
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    FakeWS.instances[0].open();
    FakeWS.instances[0].send({ type: "candle", symbol: "XAUUSD", timeframe: "15m", candle: bar(160, { complete: true }) });
    await waitFor(() => expect(analysisUrls(f)).toHaveLength(2));
    expect(screen.getByTestId("trend-class").textContent).toBe("Bullish");        // old analysis stays visible while refreshing
  });

  it("layer toggles remove only that category's drawings", async () => {
    vi.stubGlobal("fetch", router());
    render(<Page />);
    await screen.findByTestId("trend-class");
    fireEvent.click(screen.getByTestId("layer-structure"));
    const markers = m.setMarkers.mock.calls.at(-1)![0] as { text: string }[];
    expect(markers.map((x) => x.text)).toEqual(["HH", "HL"]);
    expect(m.lines).toHaveLength(3);                                                // rebuilt: trendline only (2 earlier + 1 new)
    expect(m.lines.at(-1)!.setData).toHaveBeenLastCalledWith([{ time: 100, value: 1 }, { time: 160, value: 1.4 }]);
    fireEvent.click(screen.getByTestId("layer-swings"));
    fireEvent.click(screen.getByTestId("layer-trendlines"));
    expect(m.setMarkers.mock.calls.at(-1)![0]).toEqual([]);
    expect(m.removeSeries).toHaveBeenCalled();
  });

  it("clears analysis when switching instrument and ignores a late response for the old one", async () => {
    let resolveOld: (r: Response) => void = () => {};
    const f = vi.fn((url: string) => {
      if (url.includes("/api/analysis/XAUUSD")) return new Promise<Response>((res) => { resolveOld = res; });
      if (url.includes("/api/analysis/USDJPY")) return json(analysisFor({ instrument: "USDJPY", provider_symbol: "USD_JPY", trend: { ...analysisFor().trend!, classification: "Bearish" } }));
      return history();
    });
    vi.stubGlobal("fetch", f);
    render(<Page />);
    await waitFor(() => expect(analysisUrls(f)).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Instrument"), { target: { value: "USDJPY" } });
    expect(screen.queryByTestId("trend-class")).toBeNull();                          // nothing from the old instrument remains
    await waitFor(() => expect(screen.getByTestId("trend-class").textContent).toBe("Bearish"));
    await act(async () => { resolveOld(new Response(JSON.stringify(analysisFor()), { status: 200 })); });
    expect(screen.getByTestId("trend-class").textContent).toBe("Bearish");
  });

  it("shows analysis errors, insufficient and stale states without drawing anything", async () => {
    vi.stubGlobal("fetch", router(() => json({ detail: { status: "unavailable", reason: "OANDA is down." } }, 503)));
    render(<Page />);
    await waitFor(() => expect(screen.getByTestId("analysis-message").textContent).toContain("OANDA is down."));
    expect(m.addLineSeries).not.toHaveBeenCalled();
    cleanup();

    const stale = analysisFor({ status: "stale", reason: "The latest closed 15m candle is old; analysis is withheld until data is current.", trend: null, swings: [], zones: [], events: [], trendlines: [], mtf: null, commentary: [] });
    vi.stubGlobal("fetch", router(() => json(stale)));
    render(<Page />);
    await waitFor(() => expect(screen.getByTestId("analysis-message").textContent).toContain("withheld"));
    expect(screen.queryByTestId("trend-class")).toBeNull();
    expect(m.setMarkers.mock.calls.at(-1)?.[0] ?? []).toEqual([]);
    expect(m.addLineSeries).not.toHaveBeenCalled();
  });
});
