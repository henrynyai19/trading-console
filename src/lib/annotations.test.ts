import { describe, expect, it, vi } from "vitest";
import { AnnotationLayer, buildLines, buildMarkers, buildZones, marketSessionAt, zoneRects, type ZoneSpec } from "./annotations";
import type { AnalysisResponse, Layers } from "./types";

const ALL: Layers = { zones: true, swings: true, structure: true, trendlines: true };
const base = (over: Partial<AnalysisResponse> = {}): AnalysisResponse => ({
  instrument: "EURUSD", provider_symbol: "EUR_USD", timeframe: "1h", analysis_time: 1, status: "ok", reason: null,
  freshness: { status: "fresh", last_closed_time: 1, last_close_at: 2, age_seconds: 1, trading_age_seconds: 1 }, candles_used: 100, warnings: [],
  trend: null, swings: [], zones: [], nearest_support: null, nearest_resistance: null, events: [], trendlines: [], mtf: null, commentary: [], ...over,
});
const sw = (id: string, kind: "high" | "low", label: "HH" | "LL" | "EQH", time: number) => ({ id, kind, label, price: 1.1, time, confirmed_at: time + 10, timeframe: "1h", strength: 50 });
const ev = (time: number, kind: "BOS" | "CHoCH", direction: "bullish" | "bearish") =>
  ({ id: `${kind}-${time}`, kind, direction, level: 1.1, broken_swing_time: time - 100, swing_confirmed_at: time - 50, break_time: time, confirmed_at: time + 10, instrument: "EURUSD", timeframe: "1h" });

describe("annotation builders", () => {
  const a = base({ swings: [sw("b", "low", "LL", 300), sw("a", "high", "HH", 100), sw("c", "high", "EQH", 200)], events: [ev(250, "CHoCH", "bearish")] });

  it("orders markers ascending, colours by meaning and places highs above / lows below", () => {
    const m = buildMarkers(a, ALL);
    expect(m.map((x) => x.time)).toEqual([100, 200, 250, 300]);
    expect(m.map((x) => x.text)).toEqual(["HH", "EQH", "CHoCH", "LL"]);
    expect(m[0]).toMatchObject({ position: "aboveBar", color: "#3fb68b" });
    expect(m[3]).toMatchObject({ position: "belowBar", color: "#e5594f" });
    expect(m[2]).toMatchObject({ shape: "arrowDown", position: "belowBar" });
  });

  it("omits hidden layers and anything older than the charted range", () => {
    expect(buildMarkers(a, { ...ALL, swings: false }).map((x) => x.text)).toEqual(["CHoCH"]);
    expect(buildMarkers(a, { ...ALL, structure: false }).map((x) => x.text)).toEqual(["HH", "EQH", "LL"]);
    expect(buildMarkers(a, ALL, 150).map((x) => x.text)).toEqual(["EQH", "CHoCH", "LL"]);
  });

  it("returns nothing unless the analysis is confirmed ok", () => {
    for (const status of ["stale", "insufficient_data"] as const) {
      const x = base({ status, swings: a.swings, events: a.events });
      expect([buildMarkers(x, ALL), buildLines(x, ALL), buildZones(x, ALL)]).toEqual([[], [], []]);
    }
    expect(buildMarkers(null, ALL)).toEqual([]);
  });

  it("builds horizontal break levels (CHoCH heavier) and clips trendlines to the visible range", () => {
    const x = base({
      events: [ev(250, "BOS", "bullish"), ev(500, "CHoCH", "bearish")],
      trendlines: [{ id: "t", timeframe: "1h", direction: "bullish", anchors: [{ time: 0, price: 1 }, { time: 100, price: 2 }], start_time: 0, start_price: 1, end_time: 100, end_price: 2, slope_per_bar: 0.1, touches: 2, strength: 50, status: "broken", break_time: 100, created_at: 50 }],
    });
    const l = buildLines(x, ALL, 50);
    const bos = l.find((s) => s.id === "lvl-BOS-250")!, choch = l.find((s) => s.id === "lvl-CHoCH-500")!, tl = l.find((s) => s.id === "tl-t")!;
    expect(bos.points).toEqual([{ time: 150, value: 1.1 }, { time: 250, value: 1.1 }]);
    expect([bos.width, choch.width]).toEqual([1, 2]);
    expect(tl.points).toEqual([{ time: 50, value: 1.5 }, { time: 100, value: 2 }]);   // price interpolated at the clip point
    expect(tl.style).toBe(1);                                                          // broken lines are dotted
    expect(buildLines(x, { ...ALL, trendlines: false }, 50).some((s) => s.id.startsWith("tl-"))).toBe(false);
  });

  it("only active zones are drawn", () => {
    const z = (id: string, status: "active" | "invalidated") => ({ id, instrument: "EURUSD", timeframe: "1h", type: "support" as const, origin_type: "support" as const, upper: 2, lower: 1, strength: 50, touches: 2, start_time: 10, created_at: 20, last_interaction: 30, status, role_reversed: false, role_reversal_time: null });
    const response = base({ zones: [z("nearest", "active"), z("other", "active"), z("invalidated", "invalidated")], nearest_support: "nearest" });
    expect(buildZones(response, ALL, 0).map((s) => s.id)).toEqual(["nearest"]);
    expect(buildZones(base({ zones: [z("unverified", "active")] }), ALL, 0)).toEqual([]);
  });
});

describe("zone geometry stays aligned with the chart coordinates", () => {
  const z = (type: "support" | "resistance", start: number): ZoneSpec => ({ id: type + start, type, upper: 20, lower: 10, start, strength: 80 });
  const timeToX = (t: number) => (t >= 100 && t <= 500 ? (t - 100) * 2 : null);
  const priceToY = (p: number) => 1000 - p * 10;

  it("maps price band and start time through the supplied coordinate functions", () => {
    const [r] = zoneRects([z("support", 150)], timeToX, priceToY, { from: 100, to: 500 });
    expect(r).toMatchObject({ x1: 100, y1: 800, y2: 900 });
    expect(r.x2).toBeNull();                                  // runs to the right edge
    expect(r.fill).toContain("63,182,139");                   // green for support
  });

  it("uses red for resistance, draws from the left edge for zones older than the window and skips future ones", () => {
    const rects = zoneRects([z("resistance", 50), z("resistance", 900), z("support", 150)], timeToX, priceToY, { from: 100, to: 500 });
    expect(rects.map((r) => r.id)).toEqual(["resistance50", "support150"]);
    expect(rects[0].x1).toBe(0);
    expect(rects[0].fill).toContain("229,89,79");
  });

  it("skips zones whose price is off the scale", () => {
    expect(zoneRects([z("support", 150)], timeToX, () => null, { from: 100, to: 500 })).toEqual([]);
  });
});

describe("market session grouping", () => {
  const utc = (value: string) => Math.floor(Date.parse(value) / 1000);
  it("classifies Tokyo, London, and New York sessions in their local market hours", () => {
    expect(marketSessionAt(utc("2026-02-05T03:00:00Z"))).toBe("asia");
    expect(marketSessionAt(utc("2026-02-05T09:00:00Z"))).toBe("london");
    expect(marketSessionAt(utc("2026-02-05T15:00:00Z"))).toBe("new_york");
  });
  it("accounts for daylight saving time and the Sunday FX reopen", () => {
    expect(marketSessionAt(utc("2026-07-08T13:00:00Z"))).toBe("new_york");
    expect(marketSessionAt(utc("2026-02-01T22:00:00Z"))).toBe("new_york");
  });
});

describe("AnnotationLayer", () => {
  function fake() {
    const lines: { setData: ReturnType<typeof vi.fn> }[] = [];
    const chart = { addLineSeries: vi.fn(() => { const l = { setData: vi.fn() }; lines.push(l); return l; }), removeSeries: vi.fn() };
    const series = { setMarkers: vi.fn(), attachPrimitive: vi.fn(), detachPrimitive: vi.fn() };
    return { chart, series, lines, layer: new AnnotationLayer(chart as never, series as never) };
  }
  const a = base({ events: [ev(250, "BOS", "bullish")], swings: [sw("a", "high", "HH", 100)] });

  it("replaces its drawings on every update and cleans up on destroy", () => {
    const { chart, series, layer } = fake();
    expect(series.attachPrimitive).toHaveBeenCalledTimes(1);
    layer.update(a, ALL, 0);
    expect(chart.addLineSeries).toHaveBeenCalledTimes(1);
    layer.update(a, ALL, 0);
    expect(chart.removeSeries).toHaveBeenCalledTimes(1);          // the previous line is removed before redrawing
    layer.update(null, ALL, 0);                                  // e.g. instrument switch: everything disappears
    expect(chart.removeSeries).toHaveBeenCalledTimes(2);
    expect(series.setMarkers).toHaveBeenLastCalledWith([]);
    layer.destroy();
    expect(series.detachPrimitive).toHaveBeenCalledTimes(1);
  });
});
