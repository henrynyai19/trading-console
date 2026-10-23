# Phase 7 — Live Validation and Reliability Audit

## Repository and current state

The existing project is a Next.js dashboard backed by a FastAPI service. The current preview uses the Biquote provider, supports optional OANDA and Supabase integrations, and includes candle charts, structure overlays, analysis, setup lifecycle references and historical backtesting. The repository is on branch `main`, with remote `origin` pointing to the Trading Console GitHub repository. The checkout already had a large set of uncommitted moves/deletions and generated source-layout changes before this audit; those were preserved.

The preview initially revealed a deployment mismatch: the dashboard on port 3000 was talking to an older API on port 8000 whose OpenAPI schema did not include the Phase 6 `/api/backtests` routes. I restarted the local API from this checkout. The API now advertises the Phase 6 routes, and the dashboard again shows a connected Biquote feed. This was a stale local process, not a missing route in the checked-out source.

The follow-up security fix keeps this single-user console local: frontend launch commands bind to `127.0.0.1`, the API rejects non-loopback HTTP and WebSocket clients, and both backtest endpoints share a request limit and concurrency cap. The legacy candle-count backtest now runs in a worker thread so it does not block the async request loop. No homegrown login or browser-held API key was added.

## Feature audit

| Area | Finding |
|---|---|
| OANDA adapter | Exists; credential, instrument, candle and price-stream code has mock-provider tests. No valid OANDA credentials are configured here, so no real OANDA request or stream was run. Environment defaults to practice. |
| Historical candles | Biquote provider was exercised through the running API; OANDA historical pagination is covered by provider tests. Both validate or normalize provider candles and order/deduplicate data. |
| Streaming and reconnect | Biquote stream was connected in the running app. Server and browser reconnect/backfill behavior have automated tests; browser tests simulate disconnection. |
| Freshness and stale handling | Analysis classifies fresh/stale/insufficient data and excludes unclosed/provisional candles from analysis. The feed separately identifies its upstream and market-open state. Exchange-specific intraday schedules are not modeled for every instrument. |
| Candles and overlays | Candlestick chart, support/resistance, swing, BOS/CHoCH, trendline and setup price-reference overlays exist. Browser tests check rendered chart and overlay toggles. |
| Analysis | Swings, HH/HL/LH/LL, structure, zones, trend scoring and multi-timeframe context exist and have deterministic fixture tests. |
| Trade setups | Rule-based setup candidates and closed-candle lifecycle exist. Invalid/stale analysis yields no new candidate. Supabase history is optional and was not configured. |
| Backtesting | Phase 6 structural strategy, trade ledger, costs, metrics, curves and chronological splits exist. One read-only real Biquote API backtest completed; it is not a profitability claim. |
| Dashboard/responsive | Dashboard works at desktop and mobile sizes in the 14-test browser suite. |
| Authentication/authorization | No login or per-user authorization is implemented. The API enforces a loopback-only boundary; non-loopback HTTP and WebSocket clients are rejected. Keep it local/private; public deployment requires proper access control and abuse protection. |

## Phase 7 changes

- `backend/app/main.py` — `/health` now separates provider configuration from process liveness, identifies the OANDA environment without exposing credentials, and returns active feed summaries.
- `backend/app/stream.py` — added non-sensitive feed health details: instrument, provider symbol, upstream state, market-open flag, stale flag and last tick time. Prices and credentials are omitted.
- `backend/app/candle_utils.py` — later provider revisions to already completed candles are applied as before but now emit a warning with the affected candle time.
- `backend/tests/test_candle_utils_cache.py` and `backend/tests/test_ws_api.py` — added assertions for logged completed-candle corrections and safe health responses.
- `backend/app/api_guards.py` and `backend/app/main.py` — local-peer enforcement, a 12-per-minute backtest limit per client and a two-run concurrency cap shared by both backtest routes.
- `backend/tests/test_api_guards.py` and `backend/tests/test_ws_api.py` — limiter-window and remote-peer rejection checks.
- `package.json` and `playwright.config.ts` — bind local frontend and browser-test servers to loopback; browser tests use the IPv4 loopback URL to avoid localhost's IPv6 CSS-asset cancellation on this Windows setup.
- `README.md` — links this audit report.

## Live data evidence

The checked-out app was run against the configured Biquote provider, not OANDA. Observed results:

- `GET /health`: provider `biquote`, `configured=true`, one active feed, upstream `connected`, `stale=false`, market-open flag `false` at the observation, and a provider tick timestamp. This health response reports connection state; it does not claim that the market is open.
- `GET /api/candles?symbol=XAUUSD&tf=15m&count=3`: returned Biquote candles, including a developing candle, with provider source and configured-provider mode.
- `GET /api/analysis/XAUUSD?timeframe=15m`: returned `status=ok`, `freshness=fresh`, and 192 candles used.
- `GET /api/setups/XAUUSD?timeframe=15m`: returned `status=ok`, fresh data and zero valid candidates. No setup was fabricated to fill the panel.
- A read-only `POST /api/backtests` for XAUUSD/15m and a one-day UTC window used Biquote history, evaluated 91 candles, returned two simulated trades and a deterministic candle fingerprint, and was not persisted because Supabase is not configured. The trade count is test evidence only; no performance or profitability inference is made.
- Browser inspection of the real preview returned HTTP 200, rendered chart canvases, showed `BIQUOTE · XAUUSD`, `Live` connection and `Market closed`, and reported no JavaScript console errors. One first-load candle request was aborted during client effect cleanup; the page then loaded data and connected. This is consistent with the app's development-mode request cancellation.

OANDA smoke testing was not possible: the active provider is Biquote and `OANDA_API_KEY`/`OANDA_ACCOUNT_ID` are not configured in the backend settings. No order, account mutation or broker operation was performed. Supabase is also unconfigured, so report persistence was not tested against a real database.

## Validation evidence

**Executed and passed**

- Backend: 132 passed, 1 upstream Starlette/AnyIO deprecation warning.
- Frontend unit: 39 passed (the test output includes existing React `act(...)` warnings).
- Browser E2E: 14 passed after correcting the test server URL to IPv4 loopback, including simulated data error, reconnect/backfill, instrument/timeframe switching, overlays, mobile layout and backtest display. The suite uses mocked API data; it is not live-provider evidence.
- TypeScript typecheck: passed.
- ESLint: passed.
- Next.js production build: passed; routes `/` and `/_not-found` prerendered.
- Actual local browser/API smoke checks: described above.

**Not completed or blocked**

- Real OANDA candles and stream: not run because the OANDA credentials are absent.
- Real Supabase persistence and migration: not run because Supabase is not configured.
- `npm audit --omit=dev`: the npm registry audit endpoint could not be reached, so this run did not produce a current advisory result. `pip-audit` is not installed in the backend virtual environment. No clean dependency-security result is claimed.
- Deployment logs/security checks: no deployment target was configured or inspected.
- Authentication and per-user authorization: not implemented. The API is now local-only; it is not a multi-user service.

## Remaining limitations and risks

- Biquote is an unauthenticated third-party provider. Its current connection was observed, but provider continuity and exchange-grade execution are not guaranteed.
- Candle and replay prices use provider OHLC rather than historical bid/ask ticks. Spread/slippage/commission in backtests remain assumptions supplied by the user.
- Market closure awareness uses a general weekend closure and provider market-open flags for the live stream. It does not contain authoritative instrument-specific trading calendars or holiday schedules, so stale classification around every intraday session break is not guaranteed.
- Supabase migration/persistence was not exercised against a live database. There is no authentication or per-user isolation. The backtest limiter is process-local and only protects the two costly backtest routes.
- Loopback checks are a local-use boundary, not general authorization. A public reverse proxy/deployment needs a real identity layer, proxy-aware peer handling and a shared rate limiter.
- npm/pip dependency advisories need to be rechecked from a network that can reach the package advisory services.

## Local setup and controlled validation

1. Copy `backend/.env.example` to `backend/.env`. Biquote is the default and needs no key. To use OANDA, set `MARKET_DATA_PROVIDER=oanda`, put the practice token and account ID in the backend file, and leave `OANDA_ENV=practice`. Never place these values in a `NEXT_PUBLIC_*` variable or paste them into chat.
2. In `backend/`, create/activate the documented Python virtual environment, install `requirements.txt`, and run `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
3. At the project root, copy `.env.local.example` to `.env.local`, run `npm ci`, then `npm run dev` and open `http://localhost:3000/`.
4. Verify `/health` first. Confirm `provider`, `configured`, `data_mode`, and feed `upstream`/`stale` before interpreting analysis. Then check candles, analysis and setups for the selected symbol/timeframe.
5. Run backend checks from `backend/` with `python -m pytest -q`; at the root use `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`, and `npm run e2e` (install Chromium with `npx playwright install chromium` if needed).
6. For controlled OANDA validation, use practice credentials first. Check REST candles, stream connection, market-open flag, stale/disconnect behavior and recovery. Compare timestamps/prices against the same OANDA instrument and timeframe. Do not change `OANDA_ENV` to `live` until the read-only practice checks are reviewed. This project does not place orders.

Keep OANDA credentials server-side. Current local health and preview checks validated Biquote only; the OANDA procedure above is a checklist, not a test claimed as performed.

## Local exposure hardening follow-up

The backend rejects requests whose socket peer is not loopback, including WebSocket connections. The Next.js development/start commands and Playwright production server bind to `127.0.0.1`. Both backtest routes share a 12-requests-per-minute per-client limit and at most two concurrent calculations. These controls are process-local defenses for this local single-user app. Public deployment still requires a proper authentication/authorization provider and a shared rate limiter at the hosting edge.
