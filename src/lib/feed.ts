import type { FeedState, ServerMessage } from "./types";

export interface FeedHandlers { onMessage(m: ServerMessage): void; onState(s: FeedState): void }
export interface FeedOptions { WebSocketImpl?: typeof WebSocket; base?: number; max?: number; jitter?: () => number }

export const backoffDelay = (attempt: number, base = 1000, max = 30000) => Math.min(max, base * 2 ** attempt);

/** Opens a WebSocket that reconnects with exponential backoff. Returns a function that closes it for good. */
export function openFeed(url: string, h: FeedHandlers, o: FeedOptions = {}): () => void {
  const WS = o.WebSocketImpl ?? WebSocket;
  const jitter = o.jitter ?? (() => Math.random() * 250);
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0, stopped = false, fatal = false, first = true;

  const connect = () => {
    if (stopped) return;
    h.onState(first ? "connecting" : "reconnecting");
    first = false;
    const sock = new WS(url);
    ws = sock;
    sock.onopen = () => { if (ws === sock) { attempt = 0; h.onState("open"); } };
    sock.onmessage = (e: MessageEvent) => {
      if (ws !== sock) return;
      let m: ServerMessage;
      try { m = JSON.parse(e.data as string); } catch { return; } // ignore malformed frames
      if (m.type === "error") fatal = true; // the server will not recover by itself
      h.onMessage(m);
    };
    sock.onerror = () => { try { sock.close(); } catch { /* already closed */ } };
    sock.onclose = () => {
      if (stopped || ws !== sock) return;
      if (fatal) { h.onState("closed"); return; }
      h.onState("reconnecting");
      timer = setTimeout(connect, backoffDelay(attempt++, o.base, o.max) + jitter());
    };
  };

  connect();
  return () => {
    stopped = true;
    clearTimeout(timer);
    if (ws) { ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; try { ws.close(); } catch { /* noop */ } }
  };
}
