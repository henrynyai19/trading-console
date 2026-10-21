# Phase 2 Completion Report

**Classification: implementation complete, live OANDA integration UNVERIFIED.**
No OANDA or Supabase credentials were available, so nothing here has touched the real OANDA or Supabase services.

## Built
OANDA v20 adapter (history with backward/forward paging, pricing stream, instrument availability, 429/5xx/network retry, UTC and OHLC validation, malformed-data dropping) · stream hub (one ref-counted upstream stream per symbol, heartbeat monitoring, exponential backoff, backfill after reconnect, rollover handling, reconcile) · backend `/ws` · optional Supabase cache + SQL migration · frontend live feed (reconnecting WebSocket, store that rejects stale responses and messages, chart updates, status/market/freshness/provider label) · Playwright suite.

## Test results (run in this environment)
| Check | Result |
|---|---|
| Backend pytest (mocked OANDA/Supabase, incl. WebSocket via TestClient) | **61 passed** |
| TypeScript / ESLint / production build | pass / pass / pass |
| Frontend unit tests (Vitest, jsdom, mocked chart + WebSocket) | **20 passed** |
| Real-HTTP/WebSocket integration vs local **mock** OANDA (`scripts/integration_check.py`) | **14/14 checks passed**, incl. forced stream drop, reconnect and backfill |
| Credential scan of built frontend bundle | 0 hits; leak tests on /health, /api/instruments, /api/candles and WS errors pass |
| Playwright browser tests (8 written, type-checked, listed) | **NOT EXECUTED**: Chromium cannot be downloaded in this sandbox |

Bugs found and fixed by testing: backward paging returned one candle short (OANDA `to` is inclusive); cache could never satisfy a request because the developing candle is not cached; a new feed state reconciled on its first tick.

## Not verified (must be done with real access)
1. **Live OANDA**: set credentials in `backend/.env`, run the backend, check `/health` (`provider: oanda`), `/api/instruments` (account availability, especially NAS100_USD and US30_USD), then `/api/candles` for each symbol/timeframe and watch the dashboard through a weekend close and reopen.
2. **Supabase**: run the migration, set the two env vars, confirm upsert/read and the outage fallback against the real service (PostgREST behaviour was only mocked).
3. **Playwright**: `npx playwright install chromium && npm run e2e` (needs `npm run build` first; the config does it).
4. **Visual/responsive check** in a real browser and the browser console.
5. A soak test of several hours for reconnects and rate limits.

## Known limitations
- Stream prices are sampled mid prices. Developing-candle highs/lows can miss extremes until the next official reconcile (rollover and every 60 s).
- Provisional buckets after a rollover use fixed-length math. For daily/weekly candles (OANDA aligns to 17:00 New York, weekly on Friday) and DST days it can be off by an hour until reconcile corrects it.
- Gap detection ignores the standard FX weekend only. Index CFD session breaks and holidays are treated as gaps once per process, then remembered as unfillable.
- Volume is OANDA tick volume, and the chart does not draw it yet.
- Market open/closed comes from the stream's `tradeable` flag, so it is unknown until the first price arrives.
- One OANDA price stream per symbol; OANDA limits concurrent streams, so many simultaneous symbols across users need a shared hub (current design is single-process).
- Prices are floats. Display precision is per instrument; tick-size rounding for orders belongs to Phase 4.
- No signals, and no analysis overlays, are produced.

## Ready for Phase 3
Closed candles (`complete and not provisional`) are available per symbol/timeframe through `MarketDataService.history`; see "Phase 3 hooks" in `ARCHITECTURE.md`.
