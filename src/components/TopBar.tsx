"use client";
import { useEffect, useState } from "react";
import { useConsole } from "@/store/useConsole";
import { DIGITS, SYMBOLS, TIMEFRAMES, TF_LABEL, type SignalHorizon, type Symbol, type Timeframe } from "@/lib/types";

const DOT = { live: "bg-bull", connecting: "bg-liquid", unavailable: "bg-bear", reconnecting: "bg-liquid" } as const;
const STALE_AFTER_MS = 15_000;

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id); }, [ms]);
  return now;
}

export default function TopBar() {
  const s = useConsole();
  const now = useNow();
  const syntheticPreview = s.dataMode === "test_mock_endpoint" ||
    (s.dataMode === null && process.env.NEXT_PUBLIC_SYNTHETIC_PREVIEW === "true");
  const last = s.candles.at(-1)?.close;
  const live = s.status === "live";

  const age = s.lastMessageAt ? now - s.lastMessageAt : 0;
  const stale = live && (s.feed !== "open" || s.serverStale || age > STALE_AFTER_MS);

  const connection =
    s.status === "unavailable" ? "Disconnected"
    : s.status === "connecting" ? "Connecting"
    : s.feed === "open" ? syntheticPreview ? "Synthetic stream" : s.marketOpen === false ? "Connected" : "Live"
    : s.feed === "reconnecting" || s.feed === "closed" ? "Reconnecting" : "Connecting";
  const dot = connection === "Live" ? DOT.live : connection === "Disconnected" ? DOT.unavailable : DOT.connecting;

  const freshness = syntheticPreview && live ? "Sample prices" : !live ? "No data" : stale ? "Stale data"
    : s.marketOpen === false ? "No new ticks"
    : s.lastCandleAt ? `Tick ${Math.max(0, Math.round((now - s.lastCandleAt) / 1000))}s ago` : "Waiting for first tick";
  const market = syntheticPreview && live ? "Synthetic session" : !live || s.marketOpen === null ? "Market status unknown" : s.marketOpen ? "Market open" : "Market closed";

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-edge bg-panel px-4 py-2">
      {syntheticPreview && live && <div className="w-full rounded border border-liquid px-3 py-2 text-xs font-semibold text-liquid" role="status" data-testid="synthetic-preview-banner">SYNTHETIC PREVIEW DATA — local sample prices only; not live quotes.</div>}
      <select aria-label="Instrument" value={s.symbol} onChange={(e) => s.setSymbol(e.target.value as Symbol)}
        className="rounded border border-edge bg-ink px-2 py-1 text-sm font-medium">
        {SYMBOLS.map((x) => <option key={x}>{x}</option>)}
      </select>
      <label className="flex items-center gap-2 text-xs text-mute">Signal type
        <select aria-label="Signal type" value={s.signalHorizon} onChange={(e) => s.setSignalHorizon(e.target.value as SignalHorizon)}
          className="rounded border border-edge bg-ink px-2 py-1 text-sm font-medium text-text" data-testid="signal-horizon">
          <option value="short_term">Short-term</option>
          <option value="long_term">Long-term</option>
        </select>
      </label>
      <div role="group" aria-label="Timeframe" className="flex flex-wrap gap-1">
        {TIMEFRAMES.map((t: Timeframe) => (
          <button key={t} onClick={() => s.setTimeframe(t)} aria-pressed={t === s.timeframe}
            className={`rounded px-2 py-1 text-sm ${t === s.timeframe ? "bg-support text-white" : "text-mute hover:bg-edge"}`}>
            {TF_LABEL[t]}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm md:ml-auto">
        {s.providerSymbol && <span className="text-mute" data-testid="provider-label">{syntheticPreview && live ? "SYNTHETIC PREVIEW" : (s.provider || "MARKET").toUpperCase()} · {s.providerSymbol}</span>}
        <span className="num text-lg font-semibold" data-testid="price">{last?.toFixed(DIGITS[s.symbol]) ?? "no price"}</span>
        <span className="text-mute" data-testid="market">{market}</span>
        <span className={stale ? "text-liquid" : "text-mute"} data-testid="freshness">{freshness}</span>
        <span className="flex items-center gap-2 text-mute" data-testid="connection"><i className={`h-2 w-2 rounded-full ${dot}`} />{connection}</span>
      </div>
    </header>
  );
}
