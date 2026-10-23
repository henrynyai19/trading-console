"use client";
import { useEffect, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import PriceChart from "@/components/PriceChart";
import { RightPanel, BottomPanel } from "@/components/Panels";
import { fetchAnalysis, fetchCandles, fetchSetups, wsUrl } from "@/lib/api";
import type { SetupResponse } from "@/lib/types";
import { openFeed } from "@/lib/feed";
import { keyOf, lastClosedTime, useConsole } from "@/store/useConsole";

export default function Page() {
  const symbol = useConsole((s) => s.symbol);
  const timeframe = useConsole((s) => s.timeframe);
  // Changes only when an official candle closes (not on ticks): the trigger for a fresh analysis.
  const closedAt = useConsole((s) => (s.status === "live" ? lastClosedTime(s.candles) : 0));
  const [setupResponse, setSetupResponse] = useState<SetupResponse | null>(null);
  const [setupLoading, setSetupLoading] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [chartExpanded, setChartExpanded] = useState(false);
  const setupForChart = setupResponse?.setups.find((s) => s.instrument === symbol && s.timeframe === timeframe &&
    ["confirmed", "active_reference", "entry_touched", "target_1_reached", "target_2_reached", "active"].includes(s.status)) ?? null;

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
        st().setHistory(key, r.candles, r.provider, r.providerSymbol, r.dataMode);
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

  useEffect(() => {
    if (!closedAt) {
      // Clear stale setup results when the selected market has no closed candles yet.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSetupResponse(null);
      setSetupError(null);
      setSetupLoading(false);
      return;
    }
    const ctl = new AbortController();
    let cancelled = false;
    setSetupLoading(true);
    setSetupError(null);
    fetchSetups(symbol, timeframe, ctl.signal).then((r) => {
      if (cancelled) return;
      if (r.ok) { setSetupResponse(r.setups); setSetupError(null); }
      else setSetupError(r.reason);
    }).catch(() => {}).finally(() => { if (!cancelled) setSetupLoading(false); });
    return () => { cancelled = true; ctl.abort(); };
  }, [symbol, timeframe, closedAt]);

  return (
    <div className="flex min-h-screen lg:h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <div className={chartExpanded
          ? "fixed inset-3 z-50 flex min-h-0 flex-col overflow-y-auto rounded-xl border border-edge bg-panel shadow-2xl lg:flex-row"
          : "flex min-h-0 flex-1 flex-col lg:flex-row"}>
          <main className={`flex h-[60vh] min-w-0 flex-col lg:h-auto lg:flex-1 ${chartExpanded ? "lg:min-h-0" : ""}`}>
            <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-3 py-2 text-[11px] text-mute" role="status">
              Chart and analysis use candles from the selected live market provider.
              <button
                type="button"
                onClick={() => setChartExpanded((expanded) => !expanded)}
                aria-label={chartExpanded ? "Exit expanded chart" : "Expand chart"}
                className="inline-flex shrink-0 items-center gap-1.5 rounded border border-edge bg-panel px-2.5 py-1.5 text-xs font-medium text-text transition hover:border-support hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-support"
              >
                {chartExpanded ? <Minimize2 size={14} aria-hidden="true" /> : <Maximize2 size={14} aria-hidden="true" />}
                {chartExpanded ? "Exit expanded view" : "Expand chart"}
              </button>
            </div>
            <div className="min-h-0 flex-1"><PriceChart setup={setupForChart} /></div>
          </main>
          <RightPanel setupResponse={setupResponse} setupLoading={setupLoading} setupError={setupError} />
        </div>
        {!chartExpanded && <BottomPanel />}
      </div>
    </div>
  );
}
