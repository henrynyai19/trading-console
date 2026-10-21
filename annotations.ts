import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type {
  IChartApi, ISeriesPrimitivePaneRenderer, ISeriesPrimitivePaneView, ISeriesApi, ISeriesPrimitive, SeriesAttachedParameter,
  SeriesMarker, Time, UTCTimestamp,
} from "lightweight-charts";
import type { AnalysisResponse, Layers } from "./types";

export const COLORS = { support: "#4c8dff", resistance: "#f0933a", bull: "#3fb68b", bear: "#e5594f", neutral: "#8a93a3" };
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
    for (const s of a.swings) {
      if (!s.label || s.time < minTime) continue;
      const color = s.label === "HH" || s.label === "HL" ? COLORS.bull : s.label === "LH" || s.label === "LL" ? COLORS.bear : COLORS.neutral;
      out.push({ time: T(s.time), position: s.kind === "high" ? "aboveBar" : "belowBar", color, shape: "circle", size: 0.5, text: s.label });
    }
  }
  if (layers.structure) {
    for (const e of a.events) {
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
    for (const e of a.events) {
      const from = Math.max(e.broken_swing_time, minTime);
      if (e.break_time <= from) continue;
      const c = e.direction === "bullish" ? COLORS.bull : COLORS.bear;
      out.push({ id: `lvl-${e.id}`, color: c, width: e.kind === "CHoCH" ? 2 : 1, style: e.kind === "CHoCH" ? 1 : 2,
        points: [{ time: from, value: e.level }, { time: e.break_time, value: e.level }] });
    }
  }
  if (layers.trendlines) {
    for (const t of a.trendlines) {
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
  return a.zones.filter((z) => z.status === "active")
    .map((z) => ({ id: z.id, type: z.type, upper: z.upper, lower: z.lower, start: Math.max(z.start_time, minTime), strength: z.strength }));
}

export interface ZoneRect { id: string; x1: number; x2: number | null; y1: number; y2: number; fill: string; stroke: string }

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
    const alpha = 0.1 + 0.15 * (z.strength / 100);
    out.push({ id: z.id, x1: x, x2: null, y1: Math.min(yU, yL), y2: Math.max(yU, yL), fill: rgba(base, alpha), stroke: rgba(base, 0.7) });
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
        ctx.fillStyle = r.fill;
        ctx.fillRect(x1, y1, w, h);
        ctx.fillStyle = r.stroke;
        const t = Math.max(1, Math.round(hr));
        ctx.fillRect(x1, y1, w, t);
        ctx.fillRect(x1, y1 + h - t, w, t);
      }
    });
  }
}

export class ZonePrimitive implements ISeriesPrimitive<Time> {
  private param: SeriesAttachedParameter<Time> | null = null;
  zones: ZoneSpec[] = [];
  private view: ISeriesPrimitivePaneView = { zOrder: () => "bottom", renderer: () => new ZoneRenderer(this.rects()) };
  attached(p: SeriesAttachedParameter<Time>) { this.param = p; }
  detached() { this.param = null; }
  paneViews() { return [this.view]; }
  setZones(z: ZoneSpec[]) { this.zones = z; this.param?.requestUpdate(); }
  /** Recomputed on every paint, so zones stay glued to time and price while panning and zooming. */
  rects(): ZoneRect[] {
    const p = this.param;
    if (!p || !this.zones.length) return [];
    const ts = p.chart.timeScale();
    const vr = ts.getVisibleRange();
    return zoneRects(this.zones, (t) => ts.timeToCoordinate(T(t)), (pr) => p.series.priceToCoordinate(pr),
      vr ? { from: vr.from as number, to: vr.to as number } : null);
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
