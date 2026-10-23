"use client";
import { useEffect, useRef, useState } from "react";
import { createChart, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { AnnotationLayer, marketSessionAt, SESSION_LABELS } from "@/lib/annotations";
import { useConsole } from "@/store/useConsole";
import { DIGITS, type TradeSetup } from "@/lib/types";

const toBar = (k: { time: number; open: number; high: number; low: number; close: number }) =>
  ({ time: k.time as UTCTimestamp, open: k.open, high: k.high, low: k.low, close: k.close });

export default function PriceChart({ setup = null }: { setup?: TradeSetup | null }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const layer = useRef<AnnotationLayer | null>(null);
  const setupLines = useRef<IPriceLine[]>([]);
  const seenEpoch = useRef(-1);
  const { candles, epoch, status, analysis, layers } = useConsole();
  const symbol = useConsole((s) => s.symbol);
  const firstTime = useConsole((s) => s.candles[0]?.time ?? 0);
  const [now, setNow] = useState(() => Date.now());
  const currentSession = SESSION_LABELS[marketSessionAt(Math.floor(now / 1000))];
  const verifiedZones = analysis?.status === "ok" ? [
    { name: "Support", id: analysis.nearest_support, color: "border-support/60 text-support" },
    { name: "Resistance", id: analysis.nearest_resistance, color: "border-liquid/60 text-liquid" },
  ].flatMap(({ name, id, color }) => {
    const zone = id ? analysis.zones.find((candidate) => candidate.id === id && candidate.status === "active") : null;
    return zone ? [{ name, color, range: `${zone.lower.toFixed(DIGITS[symbol])}–${zone.upper.toFixed(DIGITS[symbol])}` }] : [];
  }) : [];

  useEffect(() => {
    const interval = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!host.current) return;
    const c = createChart(host.current, {
      layout: { background: { color: "#14171c" }, textColor: "#8a93a3" },
      grid: { vertLines: { color: "#1e232b" }, horzLines: { color: "#1e232b" } },
      rightPriceScale: { borderColor: "#2a303a" }, timeScale: { borderColor: "#2a303a", timeVisible: true },
      autoSize: true,
    });
    chart.current = c;
    series.current = c.addCandlestickSeries({
      upColor: "#3fb68b", downColor: "#e5594f", wickUpColor: "#3fb68b", wickDownColor: "#e5594f", borderVisible: false,
    });
    layer.current = new AnnotationLayer(c, series.current);
    seenEpoch.current = -1;
    return () => { layer.current?.destroy(); layer.current = null; c.remove(); chart.current = null; series.current = null; };
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (epoch !== seenEpoch.current) {
      const hadData = seenEpoch.current >= 0 && candles.length > 0 && chart.current !== null;
      s.setData(candles.map(toBar));
      seenEpoch.current = epoch;
      if (candles.length && !hadData) chart.current?.timeScale().fitContent();
    } else if (candles.length) {
      s.update(toBar(candles[candles.length - 1])); // developing candle moves; a new candle appends
    }
  }, [candles, epoch]);

  // Redrawn only when the analysis, the layer toggles or the charted range change. Live ticks never reach this effect.
  useEffect(() => { layer.current?.update(analysis, layers, firstTime); }, [analysis, layers, firstTime]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    setupLines.current.forEach((line) => s.removePriceLine(line));
    setupLines.current = [];
    if (!setup || !["confirmed", "active_reference", "entry_touched", "target_1_reached", "target_2_reached", "active"].includes(setup.status)) return;
    const firstTarget = setup.targets[0];
    if (!firstTarget || typeof s.createPriceLine !== "function") return;
    const levels = [
      { price: setup.planned_entry, color: "#ffd166", title: "Entry", lineStyle: 0 as const },
      { price: setup.stop_loss, color: "#ff5b57", title: "Stop loss", lineStyle: 2 as const },
      { price: firstTarget.price, color: "#4de1a1", title: "Take profit", lineStyle: 2 as const },
    ];
    setupLines.current = levels.map((level) => s.createPriceLine({
      price: level.price, color: level.color, title: level.title, axisLabelVisible: true, lineWidth: 2, lineStyle: level.lineStyle,
    }));
  }, [setup]);

  return (
    <div className="relative h-full w-full" data-testid="chart">
      <div ref={host} className="h-full w-full" />
      <div className="absolute left-2 top-2 z-10 inline-flex items-center gap-1.5 rounded-full border border-edge bg-panel/95 px-2.5 py-1.5 text-[11px] text-text shadow" role="status" aria-label={`Current market session: ${currentSession}`} data-testid="current-session">
        <i className="h-2 w-2 rounded-full bg-support" aria-hidden="true" />
        <span className="text-mute">Session</span>
        <strong className="font-medium">{currentSession}</strong>
      </div>
      {verifiedZones.length > 0 && <div className="pointer-events-none absolute right-2 top-2 z-10 flex flex-col items-end gap-1" aria-label="Verified support and resistance levels" data-testid="verified-zones">
        {verifiedZones.map((zone) => <span key={zone.name} className={`rounded border bg-panel/95 px-2 py-1 text-[10px] font-semibold shadow ${zone.color}`}>
          {zone.name} · {zone.range}
        </span>)}
      </div>}
      {status !== "live" && (
        <div className="absolute inset-0 grid place-items-center bg-ink/80" role="status">
          <div className="max-w-sm text-center">
            <p className="text-lg font-semibold">{status === "connecting" ? "Connecting to market data" : "DATA UNAVAILABLE"}</p>
            {status === "unavailable" && (
                <p className="mt-2 text-sm text-mute">Signals are disabled until live data is connected.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
