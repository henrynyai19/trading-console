# Trading Analysis Console

Decision support only. The console does not place trades and does not treat mock prices as live data. The frontend requires Node.js 20.9 or newer; the backend requires Python 3.10 or newer.

## Setup

1. The default provider is Biquote, which the project uses for public candle history and pushed live quotes without signup or an API key. OANDA remains optional; select it with `MARKET_DATA_PROVIDER=oanda` and provide OANDA credentials in `backend/.env`.
2. Start the backend:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   # The default MARKET_DATA_PROVIDER=biquote needs no credentials.
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

3. In a second terminal, start the frontend from the repository root:

   ```powershell
   Copy-Item .env.local.example .env.local
   npm ci
   npm run dev
   ```

   Open `http://localhost:3000`. The UI connects to `http://localhost:8000` by default. If Biquote is unavailable, the app shows the feed error rather than labeling cached or synthetic data as live.

4. Optional Supabase storage: apply every SQL migration in `backend/supabase/migrations/`, then set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in `backend/.env`. The service-role key stays on the backend. The later migrations enable setup lifecycle and backtest report persistence. Candle caches are kept provider-specific; Biquote history is fetched directly to avoid mixing different feeds. Without Supabase, setup/backtest history is reported as unavailable and the completed backtest remains available in its response.

## Existing market analysis

`GET /api/analysis/{instrument}?timeframe=15m` returns trend and its rule-based score, swings (HH/HL/LH/LL), support/resistance zones, BOS/CHoCH events, trendlines, multi-timeframe bias, freshness and commentary. Stale or insufficient candles return no analysis. Only official closed candles are used; developing/provisional candles do not enter analysis.

The dashboard has instrument and timeframe selection, provider instrument labels, candlesticks, structure overlays, layer controls, connection/freshness indicators and explicit loading/error/empty states. For Biquote, NASDAQ and US30 map to its `USTEC` and `US30` index CFDs.

Biquote provides the market data independently of your TradingView account, so prices and history can differ from the broker/feed selected in TradingView. Its docs describe candle history as a combination of Yahoo Finance bootstrap data, live tick aggregation and MT5 historical feeds.

## Trade setups

`GET /api/setups/{instrument}?timeframe=15m` generates support/resistance retests, breakout retests and trend continuation plans from fresh closed-candle analysis. Each plan includes an explainable 0–100 quality score (not a success probability), entry zone, planned entry, structural stop with an ATR buffer, 1.5R/2R/3R targets, invalidation, expiry and higher-timeframe/opposing-zone context. Confirmation requires a directional close on a completed candle. Lifecycle states include `developing`, `awaiting_confirmation`, `confirmed`, `active_reference`, `entry_touched`, `target_1_reached`, `target_2_reached`, `target_3_reached`, `stopped_out`, `invalidated` and `expired`. If a candle crosses both stop and target, the stop is assumed first. Levels and R are price-based; monetary risk and position size are unavailable without account/equity and instrument contract settings. Entry touch is an analytical reference only, not a fill; the service never contacts broker order endpoints.

`GET /api/setups/{instrument}/history?timeframe=15m` reads setup snapshots when Supabase is configured and the setup migration is applied. It explicitly reports whether history persistence is available.

## Backtesting

`POST /api/backtests` runs the existing structural BOS/CHoCH strategy over a UTC date range of up to 4,900 bars, with 100 prior candles for indicator warm-up. The engine validates ordered unique timestamps and OHLC values, then evaluates only completed provider candles. A signal is evaluated after the break candle closes and enters at the next candle open. If candle OHLC data cannot establish which came first, a stop takes precedence over a target; gaps through stops exit at the worse candle open. Spread, slippage and commission are configurable per-side tick assumptions, applied once at entry and once at exit. The response includes the full trade ledger, normalized gross/cost/net R, MFE/MAE, duration, equity and drawdown series, breakdowns and chronological in-sample/validation/out-of-sample results. No account currency or broker contract value is assumed.

`GET /api/backtest/{instrument}?timeframe=1h&count=1000&risk_reward=2&spread_ticks=1&slippage_ticks=0.5` remains available for the earlier candle-count workflow. The dashboard’s **Historical backtest** section has date, target R, spread/slippage/commission and chronological period controls. Runs are bounded rather than queued as background jobs. With Supabase and the backtest migration configured, results are saved and can be retrieved at `GET /api/backtests/{backtest_id}`, `/trades` and `/equity-curve`; without it, the interface clearly marks results as unsaved.

Metrics: win rate = winning closed trades / all closed trades; expectancy = average net R; profit factor = positive net R / absolute negative net R (undefined with no losses); maximum drawdown = peak-to-trough decline of cumulative net R; MFE/MAE = within-candle favorable/adverse excursion in initial-risk units. Chronological splits do not fit or tune parameters: the same user-selected strategy configuration is replayed in all three periods, so the out-of-sample segment is a holdout report, not a statistically significant forecast.

## Configuration

Backend variables are documented in [`backend/.env.example`](backend/.env.example). Frontend configuration is in [`.env.local.example`](.env.local.example). Important backend values:

| Variable | Purpose |
|---|---|
| `MARKET_DATA_PROVIDER` | `biquote` (default, no signup) or `oanda` |
| `APP_ENV` | `development`, `test` or `production`; production configuration is validated at startup |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` | OANDA v20 credentials; required only when using OANDA |
| `OANDA_ENV` | OANDA environment: `practice` (default) or `live` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Optional server-side candle cache and setup history |
| `ALLOWED_ORIGIN(S)` | One or comma-separated browser origins; HTTPS is required in production |
| `LOG_LEVEL` | Optional backend log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `OANDA_REST_URL`, `OANDA_STREAM_URL` | Test-only mock server overrides; never set for normal use |
| `NEXT_PUBLIC_SYNTHETIC_PREVIEW` | Set to `true` only for a local mock-data preview; the header then marks prices and stream status as synthetic |

## Checks

```powershell
cd backend
 .\.venv\Scripts\python.exe -m pytest -q
cd ..
npm test
npm run typecheck
npm run lint
npm run build
```

The backend unit suite uses mocked providers. `npm run typecheck` generates Next.js route types before running TypeScript. Browser tests mock the API and can be run after installing Chromium with `npx playwright install chromium`, then `npm run e2e`. The separate HTTP integration against synthetic mock candles is test-only: run `backend/scripts/mock_oanda.py` on port 8101, point both test URL overrides at it, then run `python scripts/integration_check.py` from `backend/`.

## API

- `GET /health`, `GET /api/instruments`, `GET /api/candles?symbol&tf&count`
- `GET /api/analysis/{instrument}`, `GET /api/setups/{instrument}`, `GET /api/setups/{instrument}/history`
- `GET /api/backtest/{instrument}`, `POST /api/backtests`, `GET /api/backtests/{backtest_id}`, `GET /api/backtests/{backtest_id}/trades`, `GET /api/backtests/{backtest_id}/equity-curve`
- `WS /ws?symbol=&tf=` emits `backfill`, `candle`, `reconcile`, `status` and `error` messages

## Current hosted deployment (2026-10-23)

The current production URL is `https://trading-analysis-console.vercel.app`. It is publicly reachable; Vercel SSO protection is disabled at the owner's request. This is an analysis-only public demo: it has no login, user authorization, or per-user data isolation. No OANDA or Supabase secrets are configured and no order placement exists, but the API and backtest endpoints are open to the public and can consume the project's Vercel usage. Do not enter or add private account data or credentials. The public deployment is **not approved for a multi-user or production trading release** until authentication, authorization, shared rate limits, and data policies are implemented and verified.

The deployment build and HTTP candle endpoint succeeded. A hosted WebSocket session connected, backfilled 50 candles, and delivered changing updates, but its status reported `market_open=false`; active-market freshness is therefore not confirmed. See the dated deployment follow-up in `PHASE9_UAT_REPORT.md`.

## Private single-user access

Local use stays on `127.0.0.1`; the backend rejects non-loopback HTTP requests and checks browser WebSocket origins. For a **private** Vercel deployment, `vercel.json` routes the Next.js frontend and FastAPI backend through one deployment. Enable Vercel Authentication for **All Deployments** before deploying: Vercel requires a signed-in account with access to the project before any chart, API, or WebSocket request reaches the app. The connected Vercel team currently has one member. Keep project access limited to your own account. The backend accepts remote traffic only inside a Vercel Function when `DEPLOYMENT_MODE=vercel`; it still rejects remote peers in local mode.

This hosted mode uses Vercel's Services feature, which is currently in beta. Vercel supports FastAPI and WebSocket Functions, but an open socket stays pinned to a Function only for its configured maximum duration. The frontend reconnects and requests a fresh backfill after a disconnect. The live provider and reconnect behavior must be checked on the protected preview before relying on it. The app has no general multi-user accounts or tenant data separation; do not grant project access to other users or change deployment protection to public. Backtest endpoints remain rate limited per process, not across Vercel instances.

For the Vercel preview, set `APP_ENV=production`, `DEPLOYMENT_MODE=vercel`, and `MARKET_DATA_PROVIDER=biquote` as backend service environment variables. Biquote needs no credentials. Leave `NEXT_PUBLIC_API_URL` unset so browser requests use the same protected deployment origin. Vercel Authentication itself is configured in the project Deployment Protection settings as **All Deployments**. Do not add OANDA or Supabase secrets unless you choose and configure those services separately.

See [ARCHITECTURE.md](ARCHITECTURE.md), [PHASE2_REPORT.md](PHASE2_REPORT.md), [PHASE3_REPORT.md](PHASE3_REPORT.md), [PHASE5_REPORT.md](PHASE5_REPORT.md), [PHASE6_REPORT.md](PHASE6_REPORT.md) and [PHASE7_REPORT.md](PHASE7_REPORT.md) for design and validation records. Historical reports are not a substitute for the checks reported for the current checkout below.

## Phase 8: release readiness and deployment boundary

This repository has startup configuration validation, local liveness/readiness routes, separate process/provider/database health indicators, request IDs, structured JSON backend logs, immutable confirmed setup snapshots after the new migration is applied, and a GitHub Actions quality workflow. CI runs the backend and frontend suites, type check, lint, production build, browser suite, and package advisory audits.

**Remote deployment is not supported or claimed.** The private single-user access boundary is loopback peer enforcement plus the configured browser-origin allowlist for WebSockets. All HTTP routes, including health probes, reject non-loopback peers. A public deployment needs real authentication, server-side authorization, user isolation, rate limits, trusted proxy configuration, and database policy review. CORS or an unguessable URL is not authentication.

### Hosting decision

The supported run mode is local only. The FastAPI process needs outbound access to the selected market-data provider and an inbound WebSocket from a browser on the same machine. No hosted deployment configuration is included or verified.

### Environment and secrets

Use `.env.local.example` for frontend development, `backend/.env.example` for backend development, `backend/.env.test.example` for test runs, and the production examples only when configuring a future reviewed deployment. Only `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_SYNTHETIC_PREVIEW` are browser-visible. The API URL is public configuration, not a secret. OANDA credentials and the Supabase service key are backend-only. Add real values through local server environment settings; never commit them or put them in `NEXT_PUBLIC_*` variables. Startup rejects unsupported providers, partial OANDA credentials, mismatched Supabase settings, non-HTTPS production origins, and test endpoint overrides in production.

Backend variables: `MARKET_DATA_PROVIDER=biquote` (or `oanda`), `OANDA_ENV=practice` for read-only OANDA validation, `ALLOWED_ORIGIN(S)` for the local frontend, and optional paired `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`. If selecting OANDA, both OANDA credentials are required. `LOG_LEVEL` defaults to `INFO`. The default Biquote feed does not require credentials. `/health/ready` confirms only that a supported provider is configured; it does not claim the external provider or database is reachable. `/health` reports observed stream status and optional database configuration, but does not probe Supabase on each request. Its `status` is `degraded` until a provider feed has been observed; `/health/live` reports only that the process responds.

### Local startup and recovery

1. Start the backend using the documented command with `--host 127.0.0.1`. Do not change this to `0.0.0.0`.
2. Configure the provider in the backend environment. OANDA credentials, when used, belong only in backend settings; use `OANDA_ENV=practice` for read-only market-data validation.
3. Start the frontend with `npm run dev`, which also binds to `127.0.0.1`. Keep `NEXT_PUBLIC_API_URL` pointed at the local backend and synthetic preview disabled for live analysis.
4. Check `/health/live` for process state and `/health` for provider connection and freshness. Process liveness does not prove market data is available. Confirm candle history and WebSocket status before relying on analysis.
5. If the provider disconnects, the backend reconnects with backoff and backfills/reconciles provider candles. Restarting the backend also reopens the stream and backfills. If outbound network access is blocked by the process environment, run the local backend in a network-enabled environment; never substitute mock candles.

### Rollback and recovery

Restart the previous local frontend/backend process version and confirm `/health/live`, then recheck provider stream recovery. The process-local feed state, backtest limiter and active in-flight backtest are lost on restart; the stream reopens and backfills from provider candles. Optional setup and backtest persistence requires a configured and migrated Supabase instance. For database recovery, use a verified backup or reviewed forward-fix; do not delete tables or reverse data migrations as an automatic rollback.

### Current release checklist

- **Private local access verified:** HTTP and WebSocket services are loopback-only, with WebSocket browser-origin checking. There is no account authentication or multi-user isolation. This model is only for one user on the same machine.
- **Remote deployment blocked:** implement and verify identity, authorization, tenant isolation, trusted proxy rules and database policies before hosting or allowing off-machine browser access.
- **Provider verified locally:** with outbound network access, Biquote candle history and streaming work. OANDA practice credentials are not configured in this environment.
- **Required before remote release:** apply and verify Supabase migrations in a backed-up project if persistence is enabled; confirm RLS and service-key access under the actual project.
- **Required before remote release:** execute CI, including current npm and Python vulnerability scans; local audit endpoints may be unreachable.
- **Required before remote release:** implement authentication, server-side authorization, data isolation, and validate REST/WebSocket connectivity and recovery on the hosted stack.
- **Verified here:** see [PHASE9_UAT_REPORT.md](PHASE9_UAT_REPORT.md) for the current provider, private-boundary and regression evidence, and [PHASE8_REPORT.md](PHASE8_REPORT.md) for the previous release baseline.

## Earlier checkout validation (before the Biquote provider switch)

- Backend mocked-provider suite: 114 passed.
- Frontend unit suite: 38 passed.
- React and React DOM 19.3.1, with `@types/react` and `@types/react-dom` 19.3.0; the Next.js build no longer emits the React 18 deprecation warning.
- TypeScript typecheck: passed.
- Production Next.js build: passed.
- ESLint: passed.
- Production npm audit: 0 vulnerabilities reported. The full dependency audit reports 5 high-severity findings in Next's ESLint plugin dependency tree (`braces` via `fast-glob`); npm's suggested automatic fix would downgrade `eslint-config-next` to v14, so it was not applied.
- Playwright browser suite: 13 passed. The tests use synthetic candles and do not validate live OANDA credentials.
- On 2026-10-21, the Biquote REST endpoint returned XAUUSD candles and the app's WebSocket feed delivered a live candle update. The market was closed during the observation, and the UI showed that status. OANDA is retained as an optional provider and was not validated in this environment.
