import type { Candle } from "./types";

/** official closed (2) > official developing (1) > provisional (0). Lower ranks never overwrite higher ones. */
const rank = (c: Candle) => (c.provisional ? 0 : c.complete ? 2 : 1);

/** Ascending, de-duplicated by time. `drop` removes provisional candles the server says match no official one. */
export function mergeCandles(existing: Candle[], incoming: Candle[], drop: number[] = []): Candle[] {
  const m = new Map(existing.map((c) => [c.time, c]));
  for (const t of drop) if (m.get(t)?.provisional) m.delete(t);
  for (const c of incoming) {
    const cur = m.get(c.time);
    if (!cur || rank(c) >= rank(cur)) m.set(c.time, c);
  }
  return [...m.values()].sort((a, b) => a.time - b.time);
}
