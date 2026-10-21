import { describe, expect, it } from "vitest";
import { mergeCandles } from "./candles";
import type { Candle } from "./types";

const c = (time: number, over: Partial<Candle> = {}): Candle => ({ time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 1, complete: true, ...over });

describe("mergeCandles", () => {
  it("orders ascending and removes duplicates", () => {
    const out = mergeCandles([c(3), c(1)], [c(2), c(3), c(1)]);
    expect(out.map((x) => x.time)).toEqual([1, 2, 3]);
  });
  it("never lets a developing or provisional candle overwrite a closed official one", () => {
    const closed = c(1, { close: 1.1 });
    expect(mergeCandles([closed], [c(1, { complete: false, close: 9 }), c(1, { provisional: true, close: 8 })])[0]).toEqual(closed);
  });
  it("lets official data replace provisional data", () => {
    const out = mergeCandles([c(1, { provisional: true, close: 8 })], [c(1, { complete: false, close: 1.2 })]);
    expect(out[0].close).toBe(1.2);
  });
  it("updates the developing candle in place", () => {
    const out = mergeCandles([c(1, { complete: false })], [c(1, { complete: false, close: 1.9 })]);
    expect(out).toHaveLength(1);
    expect(out[0].close).toBe(1.9);
  });
  it("drops only provisional candles listed by the server", () => {
    const out = mergeCandles([c(1), c(2, { provisional: true, complete: false })], [], [1, 2]);
    expect(out.map((x) => x.time)).toEqual([1]);
  });
});
