import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backoffDelay, openFeed } from "./feed";

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeWS.instances.push(this); }
  close() { this.closed = true; this.onclose?.(); }
  open() { this.onopen?.(); }
  send(o: unknown) { this.onmessage?.({ data: JSON.stringify(o) }); }
}
const impl = FakeWS as unknown as typeof WebSocket;
const opts = { WebSocketImpl: impl, jitter: () => 0 };

beforeEach(() => { FakeWS.instances = []; vi.useFakeTimers(); });
afterEach(() => vi.useRealTimers());

describe("openFeed", () => {
  it("computes capped exponential backoff", () => {
    expect([0, 1, 2, 3, 10].map((a) => backoffDelay(a))).toEqual([1000, 2000, 4000, 8000, 30000]);
  });

  it("reconnects with exponential backoff and resets after a successful open", () => {
    const states: string[] = [];
    openFeed("ws://x/ws", { onMessage: () => {}, onState: (s) => states.push(s) }, opts);
    expect(FakeWS.instances).toHaveLength(1);
    FakeWS.instances[0].open();
    FakeWS.instances[0].close();                 // dropped after being open -> wait 1s
    vi.advanceTimersByTime(999); expect(FakeWS.instances).toHaveLength(1);
    vi.advanceTimersByTime(1); expect(FakeWS.instances).toHaveLength(2);
    FakeWS.instances[1].close();                 // failed to connect -> wait 2s
    vi.advanceTimersByTime(1999); expect(FakeWS.instances).toHaveLength(2);
    vi.advanceTimersByTime(1); expect(FakeWS.instances).toHaveLength(3);
    FakeWS.instances[2].open();                  // recovered: attempt counter resets
    FakeWS.instances[2].close();
    vi.advanceTimersByTime(1000); expect(FakeWS.instances).toHaveLength(4);
    expect(states).toEqual(["connecting", "open", "reconnecting", "reconnecting", "reconnecting", "reconnecting", "open", "reconnecting", "reconnecting"]);
  });

  it("delivers parsed messages and ignores malformed frames", () => {
    const got: unknown[] = [];
    openFeed("ws://x/ws", { onMessage: (m) => got.push(m), onState: () => {} }, opts);
    FakeWS.instances[0].onmessage?.({ data: "not json" });
    FakeWS.instances[0].send({ type: "status", upstream: "connected" });
    expect(got).toEqual([{ type: "status", upstream: "connected" }]);
  });

  it("closing stops reconnection and closes the socket", () => {
    const close = openFeed("ws://x/ws", { onMessage: () => {}, onState: () => {} }, opts);
    close();
    expect(FakeWS.instances[0].closed).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances).toHaveLength(1);
  });

  it("does not reconnect after the server reports unavailable data", () => {
    const states: string[] = [];
    openFeed("ws://x/ws", { onMessage: () => {}, onState: (s) => states.push(s) }, opts);
    FakeWS.instances[0].send({ type: "error", status: "unavailable", reason: "x" });
    FakeWS.instances[0].close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances).toHaveLength(1);
    expect(states.at(-1)).toBe("closed");
  });
});
