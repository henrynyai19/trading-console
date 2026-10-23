"use client";
import { useConsole } from "@/store/useConsole";
import { DIGITS, LAYER_LABEL, TF_LABEL, type AnalysisResponse, type Layers, type SetupResponse, type Symbol, type Timeframe } from "@/lib/types";
import { BacktestPanel } from "@/components/BacktestPanel";

const Row = ({ k, v, id, valueClass = "" }: { k: string; v: string; id?: string; valueClass?: string }) => (
  <div className="flex justify-between gap-3 border-b border-edge py-2 text-sm"><span className="text-mute">{k}</span><span className={`num text-right ${valueClass}`} data-testid={id}>{v}</span></div>
);
const H = ({ children }: { children: React.ReactNode }) => <h2 className="mb-1 mt-5 font-semibold first:mt-0">{children}</h2>;
const tone = (d: string) => (d === "bullish" ? "text-bull" : d === "bearish" ? "text-bear" : "text-mute");
const MOVE_UNIT: Record<Symbol, { size: number; label: string }> = {
  EURUSD: { size: 0.0001, label: "pips" }, GBPUSD: { size: 0.0001, label: "pips" }, USDJPY: { size: 0.01, label: "pips" },
  XAUUSD: { size: 0.01, label: "pips" }, NASDAQ: { size: 1, label: "points" }, US30: { size: 1, label: "points" },
};
function moveFromEntry(direction: "long" | "short", entry: number, price: number, symbol: Symbol) {
  const { size, label } = MOVE_UNIT[symbol];
  const value = (direction === "long" ? price - entry : entry - price) / size;
  return { value, label, text: `${value > 0 ? "+" : ""}${value.toFixed(1)} ${label}`, tone: value > 0 ? "text-bull" : value < 0 ? "text-bear" : "text-mute" };
}

function zoneText(a: AnalysisResponse, id: string | null, dp: number) {
  const z = a.zones.find((x) => x.id === id);
  return z ? `${z.lower.toFixed(dp)} - ${z.upper.toFixed(dp)} (strength ${z.strength.toFixed(0)})` : "none in range";
}

function Structure({ a, dp }: { a: AnalysisResponse; dp: number }) {
  const ev = a.events.at(-1);
  return (
    <>
      <Row k="Trend" v={a.trend?.classification ?? "Not enough structure"} id="trend-class" />
      <Row k="Trend score" v={a.trend ? `${a.trend.score > 0 ? "+" : ""}${a.trend.score.toFixed(0)} / 100 (rule-based)` : "n/a"} id="trend-score" />
      <Row k="Latest BOS / CHoCH" v={ev ? `${ev.direction} ${ev.kind} @ ${ev.level.toFixed(dp)}` : "none yet"} id="latest-event" />
      <Row k="Nearest support" v={zoneText(a, a.nearest_support, dp)} id="nearest-support" />
      <Row k="Nearest resistance" v={zoneText(a, a.nearest_resistance, dp)} id="nearest-resistance" />
      {a.trend && (
        <details className="mt-2 text-xs text-mute">
          <summary className="cursor-pointer">Score breakdown</summary>
          <ul className="mt-1 list-disc space-y-1 pl-4">{a.trend.explanation.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </details>
      )}
    </>
  );
}

function Mtf({ a }: { a: AnalysisResponse }) {
  const m = a.mtf;
  if (!m) return <p className="text-sm text-mute">Multi-timeframe view unavailable.</p>;
  return (
    <>
      {[m.primary, ...m.higher].map((t) => (
        <Row key={t.timeframe} k={`${TF_LABEL[t.timeframe as Timeframe]}${t === m.primary ? " (selected)" : ""}`}
          v={t.classification ?? "unavailable"} id={`mtf-${t.timeframe}`} />
      ))}
      <Row k="Alignment" v={m.alignment} id="mtf-alignment" />
      <Row k="Overall bias" v={m.bias} id="mtf-bias" />
      {m.conflicts.length > 0 && <p className="mt-1 text-xs text-mute" data-testid="mtf-conflicts">{m.conflicts.join("; ")}</p>}
    </>
  );
}

function TradePlan({ response, loading, error, live, dp, currentPrice, symbol }: { response: SetupResponse | null; loading: boolean; error: string | null; live: boolean; dp: number; currentPrice: number | null; symbol: Symbol }) {
  if (!live) return <p className="rounded border border-edge p-3 text-sm text-mute" data-testid="setup-empty">No valid setup. Signals are disabled while market data is unavailable.</p>;
  if (loading) return <p className="rounded border border-edge p-3 text-sm text-mute" data-testid="setup-loading">Checking closed-candle data for a setup…</p>;
  if (error) return <p className="rounded border border-edge p-3 text-sm text-liquid" data-testid="setup-error">Setup analysis unavailable: {error}</p>;
  if (!response) return <p className="rounded border border-edge p-3 text-sm text-mute" data-testid="setup-empty">No valid setup.</p>;
  if (response.status !== "ok") return <p className="rounded border border-edge p-3 text-sm text-mute" data-testid="setup-empty">No valid setup. {response.reason}</p>;
  const setup = response.setups[0];
  if (!setup) return <p className="rounded border border-edge p-3 text-sm text-mute" data-testid="setup-empty">No valid setup for the current closed candles.</p>;
  const movement = currentPrice == null ? null : moveFromEntry(setup.direction, setup.planned_entry, currentPrice, symbol);
  const stateLabel: Record<string, string> = {
    awaiting_confirmation: "Awaiting candle confirmation", confirmed: "Confirmed · reference pending",
    active_reference: "Reference active · no fill", entry_touched: "Reference touched · no fill", active: "Active reference",
    target_1_reached: "Target 1 reached · reference only", target_2_reached: "Target 2 reached · reference only",
    target_3_reached: "Target 3 reached · reference only", stopped_out: "Stop reached · reference only",
    invalidated: "Invalidated", expired: "Expired", developing: "Developing",
  };
  return (
    <div className="rounded border border-edge p-3 text-sm" data-testid="trade-setup">
      <div className="mb-2 flex items-center justify-between gap-2">
        <strong className={setup.direction === "long" ? "text-bull" : "text-bear"}>{setup.direction.toUpperCase()} · {setup.setup_type.replace(/_/g, " ")}</strong>
        <span className="rounded bg-ink px-2 py-1 text-xs" data-testid="setup-status">{stateLabel[setup.status] ?? setup.status}</span>
      </div>
      <Row k="Instrument / timeframe" v={`${setup.instrument} · ${TF_LABEL[setup.timeframe as Timeframe] ?? setup.timeframe}`} />
      <Row k="Quality score" v={`${setup.quality_score} / 100 · not a probability`} id="setup-score" />
      <Row k="Entry zone" v={`${setup.entry_lower.toFixed(dp)} – ${setup.entry_upper.toFixed(dp)}`} id="setup-entry" />
      <Row k="Planned entry" v={setup.planned_entry.toFixed(dp)} id="setup-planned-entry" />
      <Row k="Move from planned entry" v={movement ? `${movement.text} · ${movement.value >= 0 ? "favorable" : "adverse"}` : "waiting for live price"} id="setup-pip-move" valueClass={movement?.tone} />
      <p className="-mt-1 text-xs text-mute">Reference movement from planned entry to the latest received price; it is not a filled or realized trade and excludes spread and fees.</p>
      <Row k="Structural stop" v={`${setup.stop_loss.toFixed(dp)} · risk ${setup.risk_distance.toFixed(dp)} price units`} id="setup-stop" />
      <div className="mt-2 space-y-1" data-testid="setup-targets">
        {setup.targets.map((target) => <Row key={target.r_multiple} k={`${target.r_multiple}R target`} v={target.price.toFixed(dp)} />)}
      </div>
      <Row k="Higher-timeframe direction" v={setup.higher_timeframe_direction} />
      <Row k="Lower-timeframe confirmation" v={setup.lower_timeframe_confirmation ? "Confirmed on closed candle" : "Waiting for closed-candle confirmation"} />
      {setup.opposing_zone_price != null && <Row k={`Opposing ${setup.opposing_zone_type ?? "structure"}`} v={`${setup.opposing_zone_price.toFixed(dp)} · strength ${(setup.opposing_zone_strength ?? 0).toFixed(0)}`} />}
      <p className="mt-2 text-xs text-mute">{setup.confirmation_explanation ?? ""} {setup.invalidation_explanation ?? ""}</p>
      <p className="mt-2 text-xs text-mute">{setup.explanation}</p>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-mute">{setup.score_explanation.map((line, i) => <li key={i}>{line}</li>)}</ul>
      {(setup.caution_reasons ?? []).length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-liquid">{setup.caution_reasons.map((line, i) => <li key={i}>{line}</li>)}</ul>}
      {(response.warnings ?? []).map((warning, i) => <p key={i} className="mt-2 text-xs text-mute">{warning}</p>)}
      <p className="mt-2 text-xs text-mute">No broker order or fill is recorded. {response.history_persistent ? "Setup lifecycle history is saved in Supabase." : "Persistent setup history is unavailable or unconfirmed; configure Supabase and apply its migrations."}</p>
    </div>
  );
}

export function RightPanel({ setupResponse = null, setupLoading = false, setupError = null }: { setupResponse?: SetupResponse | null; setupLoading?: boolean; setupError?: string | null }) {
  const { status, reason, symbol, analysis, analysisState, analysisError, layers, toggleLayer } = useConsole();
  const timeframe = useConsole((s) => s.timeframe);
  const currentPrice = useConsole((s) => s.candles.at(-1)?.close ?? null);
  const live = status === "live";
  const dp = DIGITS[symbol];
  // Never show a plan left over from another instrument or timeframe while a new request is in flight.
  const matchingSetupResponse = setupResponse?.instrument === symbol && setupResponse.timeframe === timeframe ? setupResponse : null;
  const overallDirection = analysis?.status === "ok"
    ? analysis.mtf?.bias ?? analysis.trend?.classification ?? "Unavailable"
    : "Unavailable";
  const directionalTone = /bullish/i.test(overallDirection) ? "text-bull"
    : /bearish/i.test(overallDirection) ? "text-bear" : "text-mute";
  const currentSetup = matchingSetupResponse?.status === "ok" ? matchingSetupResponse.setups[0] : null;
  const firstTarget = currentSetup?.targets[0];
  const summaryMovement = currentSetup && currentPrice != null
    ? moveFromEntry(currentSetup.direction, currentSetup.planned_entry, currentPrice, symbol) : null;
  let body: React.ReactNode;
  if (!live) body = <p className="text-sm text-mute" data-testid="analysis-message">{reason || "Live data is unavailable. Select a market provider in backend/.env and restart the backend. Synthetic test feeds are blocked."}</p>;
  else if (analysisState === "error") body = <p className="text-sm text-liquid" data-testid="analysis-message">Analysis unavailable: {analysisError}</p>;
  else if (!analysis) body = <p className="text-sm text-mute" data-testid="analysis-message">Analysing...</p>;
  else if (analysis.status !== "ok") body = <p className="text-sm text-liquid" data-testid="analysis-message">{analysis.reason}</p>;
  else body = (
    <>
      <Structure a={analysis} dp={dp} />
      {analysis.reason && <p className="mt-2 text-xs text-mute" data-testid="analysis-note">{analysis.reason}</p>}
      <H>Multi-timeframe</H>
      <Mtf a={analysis} />
      <H>Market commentary</H>
      <div className="space-y-2 text-sm" data-testid="commentary">{analysis.commentary.map((c, i) => <p key={i}>{c}</p>)}</div>
      {analysis.warnings.map((w, i) => <p key={i} className="mt-2 text-xs text-liquid">{w}</p>)}
    </>
  );
  return (
    <aside className="w-full shrink-0 border-t border-edge bg-panel p-4 lg:w-80 lg:overflow-y-auto lg:border-l lg:border-t-0">
      <section className="mb-5 rounded-lg border border-support/50 bg-ink p-3" aria-label="Overall trend and trade plan" data-testid="market-plan-summary">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-mute">Overall trend · {symbol} {TF_LABEL[timeframe]}</h2>
        <p className={`mt-1 text-xl font-bold ${directionalTone}`} data-testid="overall-direction">{overallDirection}</p>
        <div className="mt-3 border-t border-edge pt-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-mute">Entry & take profit</p>
          {currentSetup && firstTarget ? (
            <div className="mt-1 space-y-1 text-sm">
              <div className="flex justify-between gap-2"><span className="text-mute">{currentSetup.direction === "long" ? "Long" : "Short"} entry</span><span className="num font-semibold" data-testid="summary-entry">{currentSetup.planned_entry.toFixed(dp)}</span></div>
              <div className="flex justify-between gap-2"><span className="text-mute">Take profit ({firstTarget.r_multiple}R)</span><span className="num font-semibold text-bull" data-testid="summary-take-profit">{firstTarget.price.toFixed(dp)}</span></div>
              <div className="flex justify-between gap-2"><span className="text-mute">Move from entry</span><span className={`num font-semibold ${summaryMovement?.tone ?? "text-mute"}`} data-testid="summary-pip-move">{summaryMovement ? `${summaryMovement.text} · ${summaryMovement.value >= 0 ? "favorable" : "adverse"}` : "waiting for live price"}</span></div>
              <p className="pt-1 text-[11px] text-mute">Rule-based reference from the selected provider’s closed candles; not an order or guaranteed fill.</p>
            </div>
          ) : setupLoading ? <p className="mt-1 text-sm text-mute">Checking for a valid setup…</p>
            : setupError ? <p className="mt-1 text-sm text-liquid">Plan unavailable: {setupError}</p>
            : <div className="mt-1 space-y-1 text-sm">
              <div className="flex justify-between gap-2"><span className="text-mute">Entry</span><span className="num" data-testid="summary-entry">—</span></div>
              <div className="flex justify-between gap-2"><span className="text-mute">Take profit</span><span className="num" data-testid="summary-take-profit">—</span></div>
              <p className="pt-1 text-[11px] text-mute">No valid setup on the latest closed candles, so no price levels are available.</p>
            </div>}
        </div>
      </section>
      <H>Current market structure</H>
      {body}
      <H>Chart layers</H>
      <div className="space-y-1 text-sm">
        {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
          <label key={k} className="flex items-center gap-2">
            <input type="checkbox" checked={layers[k]} onChange={() => toggleLayer(k)} data-testid={`layer-${k}`} />{LAYER_LABEL[k]}
          </label>
        ))}
      </div>
      <H>Trade setup</H>
      <TradePlan response={matchingSetupResponse} loading={setupLoading} error={setupError} live={live} dp={dp} currentPrice={currentPrice} symbol={symbol} />
      <BacktestPanel key={timeframe} symbol={symbol} timeframe={timeframe} />
    </aside>
  );
}

export function BottomPanel() {
  const a = useConsole((s) => s.analysis);
  const events = a?.status === "ok" ? a.events.slice(-4).reverse() : [];
  return (
    <section className="grid shrink-0 grid-cols-1 gap-px border-t border-edge bg-edge text-sm md:h-40 md:grid-cols-3">
      <div className="bg-panel p-3"><h3 className="font-semibold">Recent signals</h3><p className="mt-2 text-mute">Setup references update as each completed market candle arrives.</p></div>
      <div className="overflow-y-auto bg-panel p-3"><h3 className="font-semibold">Market structure events</h3>
        {events.length === 0 ? <p className="mt-2 text-mute">Nothing to show yet.</p> : (
          <ul className="mt-2 space-y-1" data-testid="event-list">{events.map((e) => (
            <li key={e.id} className={tone(e.direction)}>{e.direction} {e.kind} · {new Date(e.confirmed_at * 1000).toISOString().slice(5, 16).replace("T", " ")} UTC</li>))}</ul>)}
      </div>
      <div className="bg-panel p-3"><h3 className="font-semibold">Multi-timeframe alignment</h3>
        <p className="mt-2 text-mute">{a?.mtf ? a.mtf.summary : "Nothing to show yet."}</p></div>
    </section>
  );
}
