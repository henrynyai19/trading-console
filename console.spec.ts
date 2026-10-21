import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

const PROVIDER: Record<string, string> = { XAUUSD: "XAU_USD", USDJPY: "USD_JPY", NASDAQ: "NAS100_USD", US30: "US30_USD", EURUSD: "EUR_USD", GBPUSD: "GBP_USD" };
const STEP: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800 };

function series(tf: string, base: number, n = 120) {
  const step = STEP[tf];
  const last = Math.floor(Date.now() / 1000 / step) * step;
  return Array.from({ length: n }, (_, i) => {
    const t = last - (n - 1 - i) * step;
    const p = base + Math.sin(i / 7) * base * 0.002;
    return { time: t, open: p, high: p + base * 0.001, low: p - base * 0.001, close: p + base * 0.0004, volume: 10, complete: i < n - 1, provisional: false };
  });
}

// Annotation times come from the same candle series the chart shows, so they line up like real backend output.
function analysisFixture(symbol: string, tf: string) {
  const c = series(tf, symbol === "USDJPY" ? 150 : 2000);
  const at = (i: number) => c[i].time;
  const hi = (i: number) => c[i].high, lo = (i: number) => c[i].low;
  const trend = symbol === "USDJPY" ? "Bearish" : "Bullish";
  return {
    instrument: symbol, provider_symbol: PROVIDER[symbol], timeframe: tf, analysis_time: at(118) + STEP[tf], status: "ok", reason: null,
    freshness: { status: "fresh", last_closed_time: at(118), last_close_at: at(118) + STEP[tf], age_seconds: 5, trading_age_seconds: 5 }, candles_used: 119, warnings: [],
    trend: { classification: trend, score: 58, components: [], explanation: ["Swing structure: +40/40", `Classified ${trend}.`], structure_state: trend === "Bullish" ? "bullish" : "bearish", bars_since_last_event: 6 },
    swings: [
      { id: "h1", kind: "high", label: "HH", price: hi(60), time: at(60), confirmed_at: at(63) + STEP[tf], timeframe: tf, strength: 55 },
      { id: "l1", kind: "low", label: "HL", price: lo(80), time: at(80), confirmed_at: at(83) + STEP[tf], timeframe: tf, strength: 45 }],
    zones: [
      { id: "zs", instrument: symbol, timeframe: tf, type: "support", origin_type: "support", upper: lo(80) * 1.001, lower: lo(80) * 0.999, strength: 70, touches: 3, start_time: at(40), created_at: at(43) + STEP[tf], last_interaction: at(80), status: "active", role_reversed: false, role_reversal_time: null },
      { id: "zr", instrument: symbol, timeframe: tf, type: "resistance", origin_type: "resistance", upper: hi(60) * 1.001, lower: hi(60) * 0.999, strength: 60, touches: 2, start_time: at(30), created_at: at(33) + STEP[tf], last_interaction: at(60), status: "active", role_reversed: false, role_reversal_time: null }],
    nearest_support: "zs", nearest_resistance: "zr",
    events: [{ id: "e1", kind: "BOS", direction: "bullish", level: hi(60), broken_swing_time: at(60), swing_confirmed_at: at(63) + STEP[tf], break_time: at(100), confirmed_at: at(100) + STEP[tf], instrument: symbol, timeframe: tf }],
    trendlines: [{ id: "t1", timeframe: tf, direction: "bullish", anchors: [{ time: at(40), price: lo(40) }, { time: at(80), price: lo(80) }], start_time: at(40), start_price: lo(40), end_time: at(118), end_price: lo(80), slope_per_bar: 0, touches: 2, strength: 60, status: "active", break_time: null, created_at: at(83) }],
    mtf: { primary: { timeframe: tf, classification: trend, direction: trend === "Bullish" ? "bullish" : "bearish", score: 58, structure_state: "bullish", reason: null },
      higher: [{ timeframe: "4h", classification: "Transitional", direction: "neutral", score: 5, structure_state: "undetermined", reason: null }],
      alignment: "partial", structural_alignment: "partial", bias: "Leaning Bullish", conflicts: ["4H is transitional"], summary: "partial" },
    commentary: [`${tf} structure is ${trend.toLowerCase()} (rule-based score, not a probability).`],
  };
}

const status = (symbol: string, over: Record<string, unknown> = {}) =>
  JSON.stringify({ type: "status", symbol, provider_symbol: PROVIDER[symbol], market_open: true, upstream: "connected", stale: false, last_price_at: 1, server_time: 1, ...over });

async function mockBackend(page: Page, opts: { unavailable?: boolean; analysisError?: boolean } = {}) {
  const requests: URL[] = [];
  const analysisRequests: URL[] = [];
  const sockets: { symbol: string; tf: string; route: WebSocketRoute }[] = [];
  await page.route("**/api/candles**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const headers = { "access-control-allow-origin": "*" };
    if (opts.unavailable) {
      return route.fulfill({ status: 503, headers, json: { detail: { status: "unavailable", reason: "No market-data provider is configured." } } });
    }
    const symbol = url.searchParams.get("symbol")!, tf = url.searchParams.get("tf")!;
    return route.fulfill({ headers, json: { symbol, timeframe: tf, provider: "oanda", provider_symbol: PROVIDER[symbol], source: "oanda", candles: series(tf, symbol === "USDJPY" ? 150 : 2000) } });
  });
  await page.route("**/api/analysis/**", async (route) => {
    const url = new URL(route.request().url());
    analysisRequests.push(url);
    const headers = { "access-control-allow-origin": "*" };
    if (opts.analysisError) return route.fulfill({ status: 503, headers, json: { detail: { status: "unavailable", reason: "Analysis backend is down." } } });
    const symbol = url.pathname.split("/").pop()!, tf = url.searchParams.get("timeframe")!;
    return route.fulfill({ headers, json: analysisFixture(symbol, tf) });
  });
  await page.routeWebSocket(/\/ws\?/, (route) => {
    const u = new URL(route.url());
    const symbol = u.searchParams.get("symbol")!, tf = u.searchParams.get("tf")!;
    sockets.push({ symbol, tf, route });
    route.send(JSON.stringify({ type: "backfill", symbol, timeframe: tf, candles: series(tf, symbol === "USDJPY" ? 150 : 2000).slice(-5) }));
    route.send(status(symbol));
  });
  return { requests, sockets, analysisRequests };
}

test.describe("desktop", () => {
  test("dashboard loads with a rendered chart canvas, provider label and live status", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByTestId("provider-label")).toHaveText("OANDA · XAU_USD");
    await expect(page.getByTestId("connection")).toContainText("Live");
    await expect(page.getByTestId("market")).toHaveText("Market open");
    await expect(page.locator('[data-testid="chart"] canvas').first()).toBeVisible();
    expect(await page.locator('[data-testid="chart"] canvas').count()).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("DATA UNAVAILABLE")).toHaveCount(0);
  });

  test("instrument switching requests the new symbol and updates the provider label", async ({ page }) => {
    const { requests, sockets } = await mockBackend(page);
    await page.goto("/");
    await expect(page.getByTestId("connection")).toContainText("Live");
    await page.getByLabel("Instrument").selectOption("USDJPY");
    await expect(page.getByTestId("provider-label")).toHaveText("OANDA · USD_JPY");
    expect(requests.at(-1)!.searchParams.get("symbol")).toBe("USDJPY");
    await expect.poll(() => sockets.at(-1)?.symbol).toBe("USDJPY");
    await expect(page.getByTestId("price")).toHaveText(/^15\d\.\d{3}$/);
  });

  test("timeframe switching requests the new timeframe", async ({ page }) => {
    const { requests, sockets } = await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: "4H" }).click();
    await expect(page.getByRole("button", { name: "4H" })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => requests.at(-1)?.searchParams.get("tf")).toBe("4h");
    await expect.poll(() => sockets.at(-1)?.tf).toBe("4h");
  });

  test("live candle messages move the displayed price", async ({ page }) => {
    const { sockets } = await mockBackend(page);
    await page.goto("/");
    await expect.poll(() => sockets.length).toBe(1);
    const last = Math.floor(Date.now() / 1000 / 900) * 900;
    sockets[0].route.send(JSON.stringify({ type: "candle", symbol: "XAUUSD", timeframe: "15m",
      candle: { time: last, open: 2000, high: 2100, low: 1990, close: 2077.77, volume: 1, complete: false, provisional: false } }));
    await expect(page.getByTestId("price")).toHaveText("2077.77");
  });

  test("API error shows DATA UNAVAILABLE and disables signals", async ({ page }) => {
    await mockBackend(page, { unavailable: true });
    await page.goto("/");
    await expect(page.getByText("DATA UNAVAILABLE")).toBeVisible();
    await expect(page.getByText("No market-data provider is configured.")).toBeVisible();
    await expect(page.getByText(/Signals are disabled/).first()).toBeVisible();
    await expect(page.getByTestId("connection")).toContainText("Disconnected");
  });

  test("WebSocket drop shows stale/reconnecting state, then reconnects with backfill", async ({ page }) => {
    const { sockets } = await mockBackend(page);
    await page.goto("/");
    await expect.poll(() => sockets.length).toBe(1);
    await expect(page.getByTestId("connection")).toContainText("Live");
    await sockets[0].route.close({ code: 1011, reason: "test drop" });
    await expect(page.getByTestId("connection")).toContainText("Reconnecting");
    await expect(page.getByTestId("freshness")).toHaveText("Stale data");
    await expect.poll(() => sockets.length, { timeout: 8000 }).toBe(2);
    await expect(page.getByTestId("connection")).toContainText("Live");
  });

  test("desktop layout shows sidebar and puts the right panel beside the chart", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    const chart = (await page.getByTestId("chart").boundingBox())!;
    const panel = (await page.getByRole("complementary").boundingBox())!;
    expect(panel.x).toBeGreaterThanOrEqual(chart.x + chart.width - 1);
  });
});

test.describe("analysis", () => {
  test("panel shows structure, multi-timeframe overview and commentary for the selected market", async ({ page }) => {
    const { analysisRequests } = await mockBackend(page);
    await page.goto("/");
    await expect(page.getByTestId("trend-class")).toHaveText("Bullish");
    await expect(page.getByTestId("latest-event")).toContainText("bullish BOS");
    await expect(page.getByTestId("nearest-support")).toContainText("strength 70");
    await expect(page.getByTestId("mtf-bias")).toHaveText("Leaning Bullish");
    await expect(page.getByTestId("mtf-conflicts")).toContainText("4H is transitional");
    await expect(page.getByTestId("commentary")).toContainText("not a probability");
    expect(analysisRequests[0].pathname).toBe("/api/analysis/XAUUSD");
    expect(analysisRequests[0].searchParams.get("timeframe")).toBe("15m");
  });

  test("switching instrument and timeframe re-analyses and never shows the previous market's result", async ({ page }) => {
    const { analysisRequests } = await mockBackend(page);
    await page.goto("/");
    await expect(page.getByTestId("trend-class")).toHaveText("Bullish");
    await page.getByLabel("Instrument").selectOption("USDJPY");
    await expect(page.getByTestId("trend-class")).toHaveText("Bearish");
    await page.getByRole("button", { name: "4H" }).click();
    await expect.poll(() => analysisRequests.at(-1)?.searchParams.get("timeframe")).toBe("4h");
    expect(analysisRequests.at(-1)!.pathname).toBe("/api/analysis/USDJPY");
  });

  test("layer toggles work and the chart canvas survives them", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByTestId("trend-class")).toBeVisible();
    for (const k of ["zones", "swings", "structure", "trendlines"]) {
      await page.getByTestId(`layer-${k}`).uncheck();
      await expect(page.getByTestId(`layer-${k}`)).not.toBeChecked();
      await page.getByTestId(`layer-${k}`).check();
    }
    expect(await page.locator('[data-testid="chart"] canvas').count()).toBeGreaterThanOrEqual(2);
  });

  test("zones are painted on the chart canvas (pixel check) and removed when the layer is hidden", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByTestId("trend-class")).toBeVisible();
    const blue = () => page.evaluate(() => {
      let n = 0;
      document.querySelectorAll<HTMLCanvasElement>('[data-testid="chart"] canvas').forEach((cv) => {
        const ctx = cv.getContext("2d"); if (!ctx || !cv.width) return;
        const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
        for (let i = 0; i < d.length; i += 4 * 7) if (d[i + 2] > 180 && d[i] < 110 && d[i + 3] > 0) n++;   // support-blue
      });
      return n;
    });
    await expect.poll(blue).toBeGreaterThan(50);
    await page.getByTestId("layer-zones").uncheck();
    await expect.poll(blue).toBeLessThan(50);
  });

  test("analysis failure is shown plainly and nothing is drawn", async ({ page }) => {
    await mockBackend(page, { analysisError: true });
    await page.goto("/");
    await expect(page.getByTestId("analysis-message")).toContainText("Analysis backend is down.");
    await expect(page.getByTestId("trend-class")).toHaveCount(0);
  });
});

test.describe("mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("sidebar hides, the chart stays usable and nothing overflows horizontally", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "Main" })).toBeHidden();
    const chart = (await page.getByTestId("chart").boundingBox())!;
    expect(chart.width).toBeGreaterThan(300);
    expect(chart.height).toBeGreaterThan(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page.getByRole("complementary")).toBeVisible();
  });
});
