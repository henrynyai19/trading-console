# Phase 8 — Production Deployment, Monitoring, Recovery and Release Readiness

**Audit date:** 2026-10-22  
**Release state:** locally validated source; no hosting deployment performed. This checkout is not ready for public access.

## Release summary

Phase 8 adds production configuration checks, clear health states, request IDs, JSON backend logs, an immutable confirmed-setup snapshot migration, CI quality/security checks, isolated browser-test builds, and deployment/rollback documentation. The application remains analysis-only.

The main release blocker is access control: the API intentionally accepts application traffic only from loopback, and there is no login, authorization, or per-user isolation. Do not publish the API or its WebSocket endpoint yet. CORS does not provide authentication. A separate hosted frontend would be unable to use this API until a reviewed identity-aware gateway/proxy and trusted-network boundary are implemented.

## Repository and Phase 1–7 audit

The project is a Next.js 16 frontend, FastAPI backend, optional Supabase persistence, and Biquote/OANDA data adapters. `main` tracks the Trading Console GitHub repository. The working tree already contained many uncommitted file moves/deletions and source-layout changes before Phase 7; those were preserved. No changes were pushed or committed.

| Area | Current implementation | Evidence and limits |
|---|---|---|
| Market data | Biquote public REST/stream is the default; OANDA v20 is optional and requires server credentials. Provider interface, mappings, paging, validation, reconnect and backfill code exist. | Provider parsing/mapping/retry behavior is covered by backend tests. Current local Biquote candle smoke returned HTTP 503, so provider connectivity is unavailable in this observation. No real OANDA credentials were available. |
| Analysis | Closed-candle swings, HH/HL/LH/LL, BOS/CHoCH, zones, trendlines, trend score and multi-timeframe context. | Deterministic backend tests and UI fixtures pass. Stale and insufficient candles suppress analysis. |
| Setups | Rule-based candidates, confirmation, immutable confirmation data, lifecycle replay and optional Supabase history. | Backend lifecycle and API tests pass. No saved data can be checked in the local Supabase instance. |
| Backtesting | Bounded closed-candle BOS/CHoCH replay, costs, trade ledger, metrics, chronological splits and optional Supabase result persistence. | Backend and frontend tests pass. Historical outcomes are simulations, not performance claims or broker positions. |
| Chart and analysis | The active page renders the internal provider-backed candlestick chart and overlays using the same candle source as its analysis. | Browser tests cover overlays and chart updates. `TradingViewChart.tsx` exists but is not mounted by the active page; TradingView account data and TradingView-widget overlays are not part of the active flow. |
| Trading boundary | No order endpoint, broker order call, automatic trading workflow or account mutation was found. | Setup lifecycle is analytical reference state only; backtesting is separate from the live feed. |
| Database | Supabase PostgREST stores optional candle cache, setup history and completed backtest reports. RLS is enabled; privileged requests use a server-side service key. | No real Supabase connection was available. The local backend environment has a partial optional Supabase configuration; the new development behavior warns and disables persistence. Production rejects partial configuration. |

## Architecture decision

The frontend is a conventional Next.js application. The backend is a long-running FastAPI process that connects outward to a provider stream and accepts browser WebSockets. Keep those services separate unless a host is verified for both stream directions and the API access boundary is replaced.

Vercel documents Python Functions and FastAPI support, including response streaming. Render documents inbound WebSockets for web services and no fixed connection duration, which is a closer operational fit for this application's persistent provider and browser streams. The repository has no Vercel or Render project configuration, no deployment credentials, and no hosted smoke evidence. A possible future layout is a Next.js frontend on Vercel and a persistent Python web service on Render; it cannot be used with the current loopback-only API. [Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python) · [Render WebSockets](https://render.com/docs/websocket) · [Render FastAPI deployment](https://render.com/docs/deploy-fastapi).

`NEXT_PUBLIC_API_URL` is public browser configuration. The frontend derives `ws://` or `wss://` from its HTTP(S) scheme. Backend `ALLOWED_ORIGINS` accepts a comma-separated allowlist and requires HTTPS origins in production. These settings do not override the loopback peer guard.

## Implementation

- `backend/app/config.py` — validates environment names, provider selection, selected OANDA credentials/environment, HTTPS production origins, test endpoint overrides, and paired Supabase settings. In development/test, incomplete optional Supabase values warn and disable persistence; production fails startup.
- `backend/app/main.py` — separates `/health/live` (process up), `/health/ready` (supported provider configured), and local-only `/health` (configuration, observed connection, data freshness and database configuration). `live` is true only when the stream is connected and a market-open feed has a price no older than 15 seconds. Database status says `configured_unchecked`; it does not falsely claim a successful database query. Minimal liveness/readiness responses are available to hosting probes, while data routes remain loopback-only.
- `backend/app/observability.py` — emits UTC JSON logs with an allowlist of fields. HTTP responses include `X-Request-ID`; the ID carries into in-request persistence/backtest logs. Request bodies, credentials, prices and account identifiers are not logged.
- `backend/app/stream.py`, `cache.py`, `setup_store.py`, `backtest_store.py` — structured provider connect/reconnect/backfill, cache and persistence events. Existing timeouts, bounded retries, exponential reconnect backoff, cache cooldown and graceful shutdown remain in place.
- `backend/supabase/migrations/20261024000000_immutable_confirmed_setup_snapshot.sql` — additive trigger preserves a non-null confirmed setup snapshot when later lifecycle updates arrive. It has not been applied to a real Supabase project.
- `.github/workflows/ci.yml` — backend/frontend tests, Python/Node dependency audits, lint, generated-route type check, production build, Playwright, and sequential SQL migrations against disposable PostgreSQL with Supabase roles. The workflow has been added locally but has not run on GitHub.
- `playwright.config.ts`, `next.config.mjs`, `.gitignore`, `scripts/playwright-global-setup.mjs` — separate `.next-e2e` build, port 3101, and owned global setup/teardown so browser tests cannot reuse stale CSS or modify the normal preview build.
- `backend/.env.test.example`, `backend/.env.production.example`, `.env.production.example`, `README.md`, `ARCHITECTURE.md` — environment examples, production boundary, health meanings, deployment/rollback steps and limitations.

## Configuration and secrets

| Variable | Scope | Required when | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | Browser-visible frontend | Always; defaults to local API for development | Public URL only; never put a secret here. HTTPS derives WSS for the feed. |
| `NEXT_PUBLIC_SYNTHETIC_PREVIEW` | Browser-visible frontend | Optional | Must stay `false` for live market use. |
| `APP_ENV` | Backend only | Always for production hosting | `development`, `test`, or `production`. |
| `MARKET_DATA_PROVIDER` | Backend only | Production | `biquote` or `oanda`. |
| `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` | Backend secrets | Only when selecting OANDA | Both required. Keep `OANDA_ENV=practice` until practice validation is complete. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Backend; key is a secret | Only when persistence/cache is enabled | Both required together in production. Service-role key bypasses RLS. |
| `ALLOWED_ORIGINS` | Backend configuration | Hosted browser origin | Exact comma-separated origins; HTTPS is required in production. It is not auth. |
| `OANDA_REST_URL`, `OANDA_STREAM_URL` | Backend test-only | Never production | Overrides are rejected in production. |
| `LOG_LEVEL` | Backend configuration | Optional | `INFO` default. |

Secret hygiene check: only `.env.example` was tracked by Git; `.env.local` and `backend/.env` are ignored. The production browser asset scan covered 47 static assets: no credential variable names or configured OANDA/Supabase secret values were present. No non-empty OANDA/Supabase key values were found in the local environment files checked. This is a local asset scan, not a hosted or GitHub secret scan.

## Health interpretation and recovery

- `GET /health/live`: minimal process response for a host liveness probe. It does not imply the provider works.
- `GET /health/ready`: confirms supported provider configuration; it does not probe external provider or database connectivity.
- `GET /health`: loopback-only detail. `checks.market_data` describes observed stream connectivity; `checks.data_freshness` is `fresh`, `stale`, `market_closed`, `unavailable`, or `unknown`; database is `not_configured`, `configuration_incomplete`, or `configured_unchecked`.
- Provider failure produces unavailable data (currently HTTP 503) rather than synthetic or falsely live values. UI displays Disconnected/No data. Analysis and actionable setups are not emitted from invalid/stale candles.
- OANDA REST has bounded retries and honors `Retry-After`; stream reconnect uses bounded exponential backoff with jitter. Feed state, rate-limit counters and in-flight backtests are process-local and reset on restart. A reconnect backfills from official provider candles; provisional candles are reconciled instead of treated as official history.
- Setup snapshots and completed backtests persist only if Supabase is fully configured and migrations are applied. Without it, lifecycle history is unavailable and backtest results are returned but not saved. No background backtest job store exists.
- Supabase migration procedures: take a provider-managed backup/export first; apply migrations by filename order; verify the tables, RLS and service-role access; keep the backup until a restore drill succeeds. No database backup or migration was executed here. For schema recovery, restore the verified backup or apply a reviewed forward migration; do not automatically drop tables.

## Performance baseline

Before app optimization, a headless local navigation to the existing frontend on port 3000 returned HTTP 200, DOMContentLoaded at 281 ms, load at 1,002 ms and transferred 5,747 bytes. It rendered with no JavaScript page errors, but the API was unhealthy and the dashboard remained Connecting/No data; those numbers measure only the local development shell and are not production latency. The 14-test browser suite ran in 47.4 seconds after isolation. No chart/data caching or analytical shortcut was introduced.

## Verification results

**Passed on the current source**

- Backend: **139 passed**, 1 upstream Starlette/AnyIO deprecation warning.
- Frontend unit: **39 passed**.
- Browser E2E: **14 passed in 47.4 seconds**. The first baseline run reused a stale server and 3 responsive/chart tests failed because its stylesheet requests returned HTTP 500. The isolated build and owned server lifecycle corrected that test-environment issue; all 14 then passed.
- TypeScript type check: passed.
- ESLint: passed.
- Next.js production build: passed; `/` and `/_not-found` prerendered.
- Updated FastAPI process on port 8001: started with valid provider settings, served `/health` and `/health/live`, and reported optional Supabase configuration as incomplete without exposing its values.
- Browser preview on port 3001: HTTP 200 and no page errors. It showed Disconnected/No data because the local Biquote candle request returned HTTP 503. No current live market feed is claimed.
- Static production asset scan: 47 assets checked; no credential variable names or configured secret values found.

**Not verified or blocked**

- Current backend already bound to 127.0.0.1:8000 is unresponsive: `/health` exceeded a five-second timeout and the port remains owned by a process outside this execution's controllable process list. It could not be replaced safely. The updated backend is running on 127.0.0.1:8001; a temporary frontend preview is running on 127.0.0.1:3001 with that API. The existing user tab at port 3000 is unchanged and may continue to show the old disconnected backend.
- Current real Biquote REST smoke: HTTP 503. Provider connectivity remains unverified; the UI properly shows no data. OANDA credentials are absent.
- Supabase connectivity, actual migration application, RLS permissions, backup/restore and persistence: not tested against a real project. Local `.env` has incomplete optional Supabase configuration; persistence is disabled.
- Local `npm audit --omit=dev` could not reach the npm advisory endpoint. `pip-audit` is not installed. CI now runs these checks, but they have not yet executed on GitHub; no clean advisory result is claimed.
- PostgreSQL migration validation is configured in CI but could not be run locally because Docker/PostgreSQL tools are unavailable.
- Vercel/Render deployment, deployment logs, HTTPS REST/WebSocket connectivity, rollback and hosted provider connectivity: not performed. No deployment access or explicit instruction to deploy was provided.
- Authentication, authorization, user ownership/isolation and shared rate limiting: not implemented. Public hosting is blocked until these controls are designed and verified.

## Prioritized production release checklist

1. **P0 — Access control:** select an identity provider/gateway, authenticate REST and WebSocket access, define trusted proxy IPs and ensure the backend cannot be reached around the gateway. Replace the loopback-only production boundary only as part of this reviewed design. Add shared rate limiting for multi-instance hosting.
2. **P0 — Repair local service boundary:** retire or recover the stuck port 8000 API process, then verify the standard preview uses the current checkout and backend health routes.
3. **P1 — Provider smoke:** from the intended host, verify Biquote or OANDA practice candles, instrument mapping, WebSocket stream, close/provisional reconciliation, stale states, market closure, reconnect and backfill. Keep an outage visible as unavailable.
4. **P1 — Database:** decide whether persistence is needed; configure both Supabase values server-side; back up; apply all ordered migrations including the snapshot trigger; verify RLS, immutable snapshot behavior, backup and restore.
5. **P1 — CI/security:** run the new workflow on GitHub, resolve dependency audit findings, review secret scanning settings and require checks before release.
6. **P1 — Hosted rehearsal:** only after items 1–5, deploy a private preview, confirm HTTPS API and WSS browser URLs, health probes, logs/request IDs, reconnect and rollback. No domain or production deployment has been changed.

## Deployment and rollback procedure

No release should proceed until the P0 items are closed.

1. Frontend: connect the repository root to a Vercel Next.js project; use `npm ci` and `npm run build`; set public `NEXT_PUBLIC_API_URL=https://<protected-api-origin>` and `NEXT_PUBLIC_SYNTHETIC_PREVIEW=false`; redeploy after changing these build-time public values.
2. Backend: use a persistent Python Web Service (for example, Render) with service root `backend`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and liveness path `/health/live`. Set backend-only `APP_ENV=production`, provider, HTTPS `ALLOWED_ORIGINS`, and optional paired Supabase values in the host secret manager. This start configuration is not safe to expose until authenticated proxy access is implemented.
3. Migrations: backup/export; apply `backend/supabase/migrations/*.sql` in order; verify the schema and RLS; validate setup and backtest read/write behavior; document the backup identifier and restore steps.
4. Deployment verification: check `/health/live`, `/health/ready`, and authorized `/health`; then test candles, analysis, setups, browser WebSocket, provider stale/outage states, and backfill. Confirm `data_freshness` before interpreting any analysis. No broker orders are part of this application.
5. Rollback frontend using the previous known-good Vercel deployment. Roll back backend to the previous successful commit/image and verify health and provider reconnect. Process-local feed and active simulation state reset; Supabase records remain if configured. For a migration issue, use the verified backup or a reviewed forward fix; do not automatically reverse or drop data.

## Local commands

```powershell
# Backend tests
Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q

# Frontend checks (from the repository root)
Set-Location ..
npm test
npm run typecheck
npm run lint
npm run build
npm run e2e
```

For a normal local run, configure the backend on `127.0.0.1:8000` and frontend with `NEXT_PUBLIC_API_URL=http://localhost:8000`. In this environment, port 8000 is occupied by the unresponsive process, so the working Phase 8 preview uses frontend `http://localhost:3001` and backend `http://127.0.0.1:8001` instead.
