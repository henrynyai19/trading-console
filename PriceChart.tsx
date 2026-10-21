"use client";
import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { AnnotationLayer } from "@/lib/annotations";
import { useConsole } from "@/store/useConsole";

const toBar = (k: { time: number; open: number; high: number; low: number; close: number }) =>
  ({ time: k.time as UTCTimestamp, open: k.open, high: k.high, low: k.low, close: k.close });

export default function PriceChart() {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const layer = useRef<AnnotationLayer | null>(null);
  const seenEpoch = useRef(-1);
  const { candles, epoch, status, reason, analysis, layers } = useConsole();
  const firstTime = useConsole((s) => s.candles[0]?.time ?? 0);

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

  return (
    <div className="relative h-full w-full" data-testid="chart">
      <div ref={host} className="h-full w-full" />
      {status !== "live" && (
        <div className="absolute inset-0 grid place-items-center bg-ink/80" role="status">
          <div className="max-w-sm text-center">
            <p className="text-lg font-semibold">{status === "connecting" ? "Connecting to market data" : "DATA UNAVAILABLE"}</p>
            {status === "unavailable" && (
              <p className="mt-2 text-sm text-mute">{reason} Signals are disabled until live data is connected.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
