"use client";

import { useEffect, useRef } from "react";
import type { Symbol, Timeframe } from "@/lib/types";

const TV_SYMBOL: Record<Symbol, string> = {
  XAUUSD: "OANDA:XAUUSD",
  USDJPY: "OANDA:USDJPY",
  NASDAQ: "OANDA:NAS100USD",
  US30: "OANDA:US30USD",
  EURUSD: "OANDA:EURUSD",
  GBPUSD: "OANDA:GBPUSD",
};

const TV_INTERVAL: Record<Timeframe, string> = {
  "1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W",
};

export default function TradingViewChart({ symbol, timeframe }: { symbol: Symbol; timeframe: Timeframe }) {
  const host = useRef<HTMLDivElement>(null);
  const tvSymbol = TV_SYMBOL[symbol];

  useEffect(() => {
    const container = host.current;
    if (!container) return;

    container.replaceChildren();
    const widget = document.createElement("div");
    widget.className = "tradingview-widget-container__widget h-full w-full";
    const attribution = document.createElement("div");
    attribution.className = "absolute bottom-1 right-2 z-10 rounded bg-ink/80 px-2 py-1 text-[10px] text-mute";
    const link = document.createElement("a");
    link.href = `https://www.tradingview.com/symbols/${tvSymbol.replace(":", "-")}/`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${tvSymbol} chart by TradingView`;
    attribution.append(link);

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    const observer = new MutationObserver(() => {
      const embed = Array.from(container.children).find((child) => child.querySelector("iframe")) as HTMLElement | undefined;
      if (!embed) return;
      embed.style.position = "absolute";
      embed.style.inset = "0";
      embed.style.width = "100%";
      embed.style.height = "100%";
      observer.disconnect();
    });
    observer.observe(container, { childList: true, subtree: true });
    script.onerror = () => {
      const fallback = document.createElement("p");
      fallback.className = "absolute inset-0 grid place-items-center px-6 text-center text-sm text-mute";
      fallback.textContent = "TradingView could not load this chart. Try opening the symbol directly on TradingView.";
      container.append(fallback);
    };
    script.textContent = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: TV_INTERVAL[timeframe],
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",
      locale: "en",
      allow_symbol_change: false,
      calendar: false,
      hide_side_toolbar: true,
      hide_top_toolbar: false,
      hide_legend: false,
      save_image: false,
      backgroundColor: "#14171c",
      gridColor: "rgba(42, 48, 58, 0.7)",
      support_host: "https://www.tradingview.com",
    });

    container.append(widget, script, attribution);
    return () => { observer.disconnect(); container.replaceChildren(); };
  }, [timeframe, tvSymbol]);

  return <div ref={host} className="tradingview-widget-container relative h-full min-h-[240px] w-full overflow-hidden" data-testid="tradingview-chart" />;
}
