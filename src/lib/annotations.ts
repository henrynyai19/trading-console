import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter,
  SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import type { AnalysisResponse, Layers } from "./types";

export const COLORS = { support: "#3fb68b", resistance: "#e5594f", bull: "#3fb68b", bear: "#e5594f", neutral: "#8a93a3" };
export type MarketSession = "sydney" | "asia" | "london" | "new_york" | "closed";
export const SESSION_LABELS: Record<MarketSession, string> = { sydney: "Sydney", asia: "Asia", london: "London", new_york: "New York", closed: "No major session" };
const localClock = (zone: string) => new Intl.DateTimeFormat("en-GB", { timeZone: zone, weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const SESSION_CLOCKS = {
  new_york: localClock("America/New_York"), london: localClock("Europe/London"),
  asia: localClock("Asia/Tokyo"), sydney: localClock("Australia/Sydney"),
};
function openInSession(timestamp: number, session: keyof typeof SESSION_CLOCKS, start: number, end: number) {
  const parts = Object.fromEntries(SESSION_CLOCKS[session].formatToParts(new Date(timestamp * 1000)).map((p) => [p.type, p.value]));
  const weekday = parts.weekday;
  const minute = Number(parts.hour) * 60 + Number(parts.minute);
  // Standard weekday cash-market hours; Sunday New York is included for the FX weekly reopen.
  const weekdayOpen = weekday !== "Sat" && weekday !== "Sun" && minute >= start && minute < end;
  return weekdayOpen || (session === "new_york" && weekday === "Sun" && minute >= 17 * 60);
}
/** Major-session bucket for a UTC candle. Overlaps are assigned to the session that opened latest by priority. */
export function marketSessionAt(timestamp: number): MarketSession {
  if (openInSession(timestamp, "new_york", 8 * 60, 17 * 60)) return "new_york";
  if (openInSession(timestamp, "london", 8 * 60, 17 * 60)) return "london";
  if (openInSession(timestamp, "asia", 9 * 60, 18 * 60)) return "asia";
  if (openInSession(timestamp, "sydney", 8 * 60, 17 * 60)) return "sydney";
  return "closed";
}
const rgba = (hex: string, a: number) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
const T = (t: number) => t as UTCTimestamp;

/** Swing labels and BOS/CHoCH labels, ascending by time (a lightweight-charts requirement). Hidden-layer items are omitted. */
export function buildMarkers(a: AnalysisResponse | null, layers: Layers, minTime = 0): SeriesMarker<Time>[] {
  if (!a || a.status !== "ok") return [];
  const out: SeriesMarker<Time>[] = [];
  if (layers.swings) {
    // Keep the latest pivots visible; older labels pile up and obscure candles on busy charts.
    for (const s of [...a.swings].filter((x) => x.label).sort((x, y) => x.time - y.time).slice(-3)) {
      if (!s.label || s.time < minTime) continue;
      const color = s.label === "HH" || s.label === "HL" ? COLORS.bull : s.label === "LH" || s.label === "LL" ? COLORS.bear : COLORS.neutral;
      out.push({ time: T(s.time), position: s.kind === "high" ? "aboveBar" : "belowBar", color, shape: "circle", size: 0.5, text: s.label });
    }
  }
  if (layers.structure) {
    for (const e of [...a.events].sort((x, y) => x.break_time - y.break_time).slice(-2)) {
      if (e.break_time < minTime) continue;
      const bull = e.direction === "bullish";
      out.push({ time: T(e.break_time), position: bull ? "aboveBar" : "belowBar", color: bull ? COLORS.bull : COLORS.bear, shape: bull ? "arrowUp" : "arrowDown", text: e.kind });
    }
  }
  return out.sort((x, y) => (x.time as number) - (y.time as number));
}

export interface LineSpec { id: string; color: string; width: 1 | 2; style: 0 | 1 | 2; points: { time: number; value: number }[] }

/** Interpolates a price at time t on the line through two points (linear in time). */
const lerp = (t0: number, p0: number, t1: number, p1: number, t: number) => p0 + ((p1 - p0) * (t - t0)) / (t1 - t0);

/** Break-level segments (broken swing -> break candle) and trendlines. Clipped to the charted range; never extended into the future. */
export function buildLines(a: AnalysisResponse | null, layers: Layers, minTime = 0): LineSpec[] {
  if (!a || a.status !== "ok") return [];
  const out: LineSpec[] = [];
  if (layers.structure) {
    for (const e of [...a.events].sort((x, y) => x.break_time - y.break_time).slice(-2)) {
      const from = Math.max(e.broken_swing_time, minTime);
      if (e.break_time <= from) continue;
      const c = e.direction === "bullish" ? COLORS.bull : COLORS.bear;
      out.push({ id: `lvl-${e.id}`, color: c, width: e.kind === "CHoCH" ? 2 : 1, style: e.kind === "CHoCH" ? 1 : 2,
        points: [{ time: from, value: e.level }, { time: e.break_time, value: e.level }] });
    }
  }
  if (layers.trendlines) {
    // Show only the strongest active/broken guide in each direction.
    const selected = ["bullish", "bearish"].flatMap((direction) => a.trendlines
      .filter((t) => t.direction === direction)
      .sort((x, y) => y.strength - x.strength || y.end_time - x.end_time)
      .slice(0, 1));
    for (const t of selected) {
      if (t.end_time <= minTime) continue;
      const start = Math.max(t.start_time, minTime);
      const broken = t.status === "broken";
      const c = t.direction === "bullish" ? COLORS.bull : COLORS.bear;
      out.push({ id: `tl-${t.id}`, color: broken ? rgba(c, 0.45) : c, width: 1, style: broken ? 1 : 0,
        points: [{ time: start, value: lerp(t.start_time, t.start_price, t.end_time, t.end_price, start) }, { time: t.end_time, value: t.end_price }] });
    }
  }
  return out;
}

export interface ZoneSpec { id: string; type: "support" | "resistance"; upper: number; lower: number; start: number; strength: number }
export function buildZones(a: AnalysisResponse | null, layers: Layers, minTime = 0): ZoneSpec[] {
  if (!a || a.status !== "ok" || !layers.zones) return [];
  const nearest = { support: a.nearest_support, resistance: a.nearest_resistance };
  return (["support", "resistance"] as const).flatMap((type) => a.zones
    .filter((z) => z.status === "active" && z.type === type && z.id === nearest[type])
    .map((z) => ({ id: z.id, type: z.type, upper: z.upper, lower: z.lower, start: Math.max(z.start_time, minTime), strength: z.strength })));
}

export interface ZoneRect { id: string; type: "support" | "resistance"; x1: number; x2: number | null; y1: number; y2: number; fill: string; stroke: string }

/** Pure geometry: media-space rectangles from chart coordinate callbacks. Zones start at their first swing and run to the right edge. */
export function zoneRects(
  zones: ZoneSpec[], timeToX: (t: number) => number | null, priceToY: (p: number) => number | null,
  visible: { from: number; to: number } | null,
): ZoneRect[] {
  const out: ZoneRect[] = [];
  for (const z of zones) {
    const yU = priceToY(z.upper), yL = priceToY(z.lower);
    if (yU === null || yL === null) continue;
    let x = timeToX(z.start);
    if (x === null) {
      if (visible && z.start < visible.from) x = 0;       // began before the visible window: draw from the left edge
      else continue;                                      // begins to the right of the window: nothing to draw yet
    }
    const base = z.type === "support" ? COLORS.support : COLORS.resistance;
    const alpha = 0.10 + 0.06 * (z.strength / 100);
    out.push({ id: z.id, type: z.type, x1: x, x2: null, y1: Math.min(yU, yL), y2: Math.max(yU, yL), fill: rgba(base, alpha), stroke: rgba(base, 0.96) });
  }
  return out;
}

class ZoneRenderer implements ISeriesPrimitivePaneRenderer {
  constructor(private rects: ZoneRect[]) {}
  draw(target: CanvasRenderingTarget2D): void {
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      for (const r of this.rects) {
        const x1 = Math.round(r.x1 * hr), y1 = Math.round(r.y1 * vr), h = Math.max(1, Math.round((r.y2 - r.y1) * vr));
        const w = bitmapSize.width - x1;
        if (w <= 0) continue;
        const y2 = y1 + h;
        const visibleTop = Math.max(0, y1), visibleBottom = Math.min(bitmapSize.height, y2);
        if (visibleBottom <= visibleTop) continue;
        ctx.fillStyle = r.fill;
        ctx.fillRect(x1, visibleTop, w, visibleBottom - visibleTop);
        ctx.fillStyle = r.stroke;
        const t = Math.max(2, Math.round(2 * hr));
        if (y1 >= 0) ctx.fillRect(x1, y1, w, t);
        if (y2 <= bitmapSize.height) ctx.fillRect(x1, y2 - t, w, t);
      }
    });
  }
}

export class ZonePrimitive implements ISeriesPrimitive<Time> {
  private param: SeriesAttachedParameter<Time> | null = null;
  zones: ZoneSpec[] = [];
  private view: ISeriesPrimitivePaneView = { zOrder: () => "top", renderer: () => new ZoneRenderer(this.rects()) };
  attached(p: SeriesAttachedParameter<Time>) { this.param = p; }
  detached() { this.param = null; }
  paneViews() { return [this.view]; }
  setZones(z: ZoneSpec[]) { this.zones = z; this.param?.requestUpdate(); }
  /** Recomputed on every paint, so zones stay glued to time and price while panning and zooming. */
  rects(): ZoneRect[] {
    const p = this.param;
    if (!p || !this.zones.length) return [];
    try {
      const ts = p.chart.timeScale();
      const vr = ts.getVisibleRange();
      return zoneRects(this.zones, (t) => ts.timeToCoordinate(T(t)), (pr) => p.series.priceToCoordinate(pr),
        vr ? { from: vr.from as number, to: vr.to as number } : null);
    } catch {
      // Lightweight Charts can throw while its first visible range is not ready yet.
      // An empty annotation frame is safe; the next chart update will render the zones.
      return [];
    }
  }
}

/** Owns every analysis drawing on one chart. update() runs when the analysis or the layer toggles change, never per tick. */
export class AnnotationLayer {
  private prim = new ZonePrimitive();
  private lines: ISeriesApi<"Line">[] = [];
  constructor(private chart: IChartApi, private series: ISeriesApi<"Candlestick">) { series.attachPrimitive(this.prim); }

  update(a: AnalysisResponse | null, layers: Layers, minTime = 0) {
    this.clearLines();
    this.series.setMarkers(buildMarkers(a, layers, minTime));
    this.prim.setZones(buildZones(a, layers, minTime));
    for (const spec of buildLines(a, layers, minTime)) {
      const s = this.chart.addLineSeries({ color: spec.color, lineWidth: spec.width, lineStyle: spec.style, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(spec.points.map((p) => ({ time: T(p.time), value: p.value })));
      this.lines.push(s);
    }
  }

  private clearLines() {
    for (const l of this.lines) { try { this.chart.removeSeries(l); } catch { /* chart already disposed */ } }
    this.lines = [];
  }

  destroy() {
    this.clearLines();
    try { this.series.detachPrimitive(this.prim); } catch { /* disposed */ }
  }
}
