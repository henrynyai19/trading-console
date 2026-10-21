"use client";
import { useConsole } from "@/store/useConsole";
import { DIGITS, LAYER_LABEL, TF_LABEL, type AnalysisResponse, type Layers, type Timeframe } from "@/lib/types";

const Row = ({ k, v, id }: { k: string; v: string; id?: string }) => (
  <div className="flex justify-between gap-3 border-b border-edge py-2 text-sm"><span className="text-mute">{k}</span><span className="num text-right" data-testid={id}>{v}</span></div>
);
const H = ({ children }: { children: React.ReactNode }) => <h2 className="mb-1 mt-5 font-semibold first:mt-0">{children}</h2>;
const tone = (d: string) => (d === "bullish" ? "text-bull" : d === "bearish" ? "text-bear" : "text-mute");

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

export function RightPanel() {
  const { status, symbol, analysis, analysisState, analysisError, layers, toggleLayer } = useConsole();
  const live = status === "live";
  const dp = DIGITS[symbol];
  let body: React.ReactNode;
  if (!live) body = <p className="text-sm text-mute" data-testid="analysis-message">No data. Analysis is unavailable.</p>;
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
      <p className="rounded border border-edge p-3 text-sm text-mute">No Trade. {live ? "Entries, stops and targets arrive in Phase 4." : "Signals are disabled while data is unavailable."}</p>
    </aside>
  );
}

export function BottomPanel() {
  const a = useConsole((s) => s.analysis);
  const events = a?.status === "ok" ? a.events.slice(-4).reverse() : [];
  return (
    <section className="grid shrink-0 grid-cols-1 gap-px border-t border-edge bg-edge text-sm md:h-40 md:grid-cols-3">
      <div className="bg-panel p-3"><h3 className="font-semibold">Recent signals</h3><p className="mt-2 text-mute">Signals arrive in Phase 4.</p></div>
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
