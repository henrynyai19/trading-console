"use client";
import { useEffect } from "react";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import PriceChart from "@/components/PriceChart";
import { RightPanel, BottomPanel } from "@/components/Panels";
import { fetchAnalysis, fetchCandles, wsUrl } from "@/lib/api";
import { openFeed } from "@/lib/feed";
import { keyOf, lastClosedTime, useConsole } from "@/store/useConsole";

export default function Page() {
  const symbol = useConsole((s) => s.symbol);
  const timeframe = useConsole((s) => s.timeframe);
  // Changes only when an official candle closes (not on ticks): the trigger for a fresh analysis.
  const closedAt = useConsole((s) => (s.status === "live" ? lastClosedTime(s.candles) : 0));

  useEffect(() => {
    const key = keyOf(symbol, timeframe);
    const ctl = new AbortController();
    const st = useConsole.getState;
    let cancelled = false;
    let closeFeed = () => {};

    fetchCandles(symbol, timeframe, ctl.signal)
      .then((r) => {
        if (cancelled) return; // a newer selection owns the screen
        if (!r.ok) { st().setUnavailable(key, r.reason); return; }
        st().setHistory(key, r.candles, r.providerSymbol);
        closeFeed = openFeed(wsUrl(symbol, timeframe), {
          onMessage: (m) => st().applyMessage(key, m),
          onState: (s) => st().setFeedState(key, s),
        });
      })
      .catch(() => {});

    return () => { cancelled = true; ctl.abort(); closeFeed(); }; // unsubscribes the old stream
  }, [symbol, timeframe]);

  useEffect(() => {
    if (!closedAt) return;
    const key = keyOf(symbol, timeframe);
    const ctl = new AbortController();
    const st = useConsole.getState;
    let cancelled = false;
    st().setAnalysisLoading(key);
    const id = setTimeout(() => {
      fetchAnalysis(symbol, timeframe, ctl.signal)
        .then((r) => { if (!cancelled) (r.ok ? st().setAnalysis(key, r.analysis) : st().setAnalysisError(key, r.reason)); })
        .catch(() => {});
    }, 150); // coalesces a burst of reconcile/backfill updates into one request
    return () => { cancelled = true; clearTimeout(id); ctl.abort(); };
  }, [symbol, timeframe, closedAt]);

  return (
    <div className="flex min-h-screen lg:h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row"><main className="h-[60vh] min-w-0 lg:h-auto lg:flex-1"><PriceChart /></main><RightPanel /></div>
        <BottomPanel />
      </div>
    </div>
  );
}
