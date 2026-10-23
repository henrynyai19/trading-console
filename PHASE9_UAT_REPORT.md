# Phase 9 — User Acceptance and Release Assessment

## Phase 9.1 — blocker remediation update (2026-10-22)

This addendum is the current acceptance result and supersedes conflicting Phase 9 baseline findings below.

### Current release decision

**PASS WITH LIMITATIONS for the private, single-user local preview using Biquote.** Real Biquote historical candles and a provider WebSocket session were verified. The preview now renders the market chart and analysis. At the observation time the feed reported **Market closed**, so sustained open-market tick freshness was not proven. OANDA practice was not tested because OANDA credentials are not configured. **Remote/public deployment remains BLOCKED**: this build has no login or multi-user authorization and supports localhost access only. This status is limited to local analytical use and is not a trading-performance claim.

### 503 reproduction, root cause, and correction

- The failing browser request was `GET http://127.0.0.1:8001/api/candles?symbol=XAUUSD&tf=15m`; it returned HTTP 503 with `Biquote market-data request failed (ConnectError).` The server log recorded a 503 in under 10 ms. The selected configuration was `MARKET_DATA_PROVIDER=biquote`; this provider does not require credentials. No OANDA credentials were present, but OANDA was not selected, so missing OANDA credentials and practice/live host mismatch did not cause this failure. Symbol and timeframe were valid.
- A direct, read-only request to Biquote's public active-instrument and XAUUSD 15-minute candle endpoints returned HTTP 200 and DNS resolved. The same backend code then returned `ConnectError` when it was running in the network-restricted process context used for the prior preview. This establishes the cause: **the backend process lacked outbound network access in that execution context**, rather than an upstream Biquote outage, invalid credentials, bad mapping, candle parsing failure, timeout, or deployment setting.
- The local backend was restarted on `127.0.0.1:8001` in a network-enabled process context with the existing Biquote configuration. No credentials were added or changed. Retest returned HTTP 200 with provider `biquote`, provider symbol `XAUUSD`, timeframe `15m`, and three candles. Timestamps were unique and ascending (12:15, 12:30, 12:45 UTC); all prices were positive and each candle satisfied the OHLC high/low bounds. The latest, still-forming candle correctly had `complete=false`; the prior two were complete. The app does not substitute synthetic candles.
- The frontend reloaded on `http://localhost:3001/` and displayed a chart, provider label `BIQUOTE · XAUUSD`, a current quote, analysis, and `Market closed`. Browser console errors and HTTP error responses were absent in the successful page capture. One initial history fetch was canceled by browser navigation (`ERR_ABORTED`); the live WebSocket backfill supplied the chart data.
- The Biquote adapter now turns connection failures and timeouts into actionable, secret-safe messages and logs only provider/error class/failure kind. `/health/live` remains a process check; `/health` reports `degraded` until a stream has been observed and preserves separate provider/freshness fields. Thus a successful liveness response does not imply market data is available.

### Streaming evidence

- The actual configured Biquote stream established a backend WebSocket session. The browser received a backfill of 50 candles; all 50 timestamps were unique and strictly ascending. The stream reported `upstream=connected`, `stale=false`, and delivered candle updates. The UI showed a quote and Live connection while separately labelling the market closed.
- I closed the browser's actual provider-backed WebSocket during acceptance. The frontend reopened it, received a second 50-candle backfill with unique ascending timestamps, and returned to Live. This verifies the browser disconnect/reconnect and backfill path against the real provider.
- I did not interrupt Biquote's upstream network connection or observe an open-market sustained tick sequence. Automatic upstream recovery after a real vendor/network outage therefore remains verified by existing mock-provider regression tests only, not by a live outage simulation. OANDA streaming was not tested.

### Private access-control model

The supported deployment choice is **private single-user localhost**. All HTTP routes, including `/health/live` and `/health/ready`, now reject non-loopback peers with HTTP 403. The WebSocket route rejects non-loopback peers and rejects a browser `Origin` outside the configured local allowlist. The front and backend commands bind to `127.0.0.1`. Automated tests cover remote REST rejection and untrusted/allowed WebSocket origins. No login or per-user database isolation exists; local account authentication is not applicable to this same-machine-only model. A reverse proxy on the same host would appear as loopback and is explicitly outside the supported boundary. Do not expose this app remotely; hosted multi-user access remains BLOCKED until identity, authorization, tenant isolation, trusted proxy controls and database policies are implemented and tested.

### Phase 9.1 changes and verification

Changed files: `backend/app/providers/biquote.py` (safe actionable connection/timeout errors and structured failure logs); `backend/app/main.py` (degraded health status and strict local HTTP/WebSocket origin boundary); `backend/tests/test_biquote_provider.py` (new failure/API/parameter regression tests); `backend/tests/test_api_guards.py` and `backend/tests/test_ws_api.py` (remote access and WebSocket origin tests); `README.md` and `ARCHITECTURE.md` (local-only access model and network-enabled provider startup instructions).

| Check | Result |
|---|---|
| Backend `backend\\.venv\\Scripts\\python.exe -m pytest -q` | **151 passed**, 1 upstream Starlette/AnyIO deprecation warning, 5.38s. |
| Frontend `npm test` | **39 passed**, 4 files. |
| Browser `npm run e2e` | **14 passed**, 47.1s. Mocked API/stream regression scenarios remain labelled as mocked. |
| `npm run lint` | **PASS**, exit 0. |
| `npm run typecheck` | **PASS**, rerun after build-generated type configuration. |
| Production build with `NEXT_DIST_DIR=.next-phase9-build` | **PASS**, static routes generated. |
| Real Biquote REST candle request | **PASS**, HTTP 200, XAUUSD/15m, three valid ordered unique candles; latest forming bar correctly incomplete. |
| Real Biquote WebSocket | **PASS WITH LIMITATIONS**, connected, backfill and browser reconnect verified; market closed and no upstream outage forced. |
| Local private access boundary | **PASS**, remote REST/liveness denial and WebSocket-origin tests pass. |
| OANDA practice feed, Supabase persistence, remote deployment | **NOT VERIFIED / BLOCKED**, no configured credentials, database access, or deployment. |

Existing Phase 9 automation remains intact; no assertions were weakened. The previous Phase 9 baseline table below is historical and should not be read as the current provider result.

**Execution date:** 2026-10-22 (Africa/Johannesburg)  
**Checkout:** local `trading-console`, branch `main`; working tree contains pre-existing moves/deletions and Phase 8 work. No commit or deployment was made.  
**Environment:** Windows PowerShell; Node v24.17.0, npm 11.13.0, Python 3.12.14 from `backend/.venv`; browser tests use Playwright Chromium and a separate Next server on `127.0.0.1:3101`. The local preview is `http://localhost:3001/`, frontend API target `http://127.0.0.1:8001`.

## Phase 9.2 Vercel private deployment preparation (2026-10-23)

**Status: BLOCKED pending deployment-tool access and remote acceptance checks. No deployment has been created.** The user authorized a private Vercel preview. The available Vercel connection was removed from this session before a project could be configured or deployed; the Vercel CLI is not installed. The Vercel team lookup had shown one member and no project named `trading-console`.

Prepared the code for Vercel's multi-service deployment model in `vercel.json`: Next.js serves the dashboard; FastAPI receives `/api/*`, `/health*`, documentation routes and `/ws` from the same deployment origin. Production frontend requests now use same-origin routes when `NEXT_PUBLIC_API_URL` is unset. The backend's hosted mode requires both `DEPLOYMENT_MODE=vercel` and Vercel's runtime `VERCEL=1`; local mode keeps rejecting remote peers. Hosted WebSockets require a secure same-origin `Origin`. The Vercel project must have Vercel Authentication set to **All Deployments** before deploying. Only the single team owner currently has Vercel membership; no other users should be granted access.

For deployment, configure backend service environment variables `APP_ENV=production`, `DEPLOYMENT_MODE=vercel`, and `MARKET_DATA_PROVIDER=biquote`; no provider secret is required. Do not set `NEXT_PUBLIC_API_URL` for same-origin routing. Do not configure OANDA/Supabase secrets unless separately chosen. Use an actual Vercel Function deployment and ensure protection is active before any deployment URL becomes available.

Vercel Services are currently beta. The Vercel function WebSocket lifetime is bounded by its maximum duration, so browser reconnect/backfill must be accepted on the hosted preview; real provider access and long-running freshness remain unverified. Rate limiting and feed state are process-local, so they do not provide distributed coordination across serverless instances. The app has no general user authentication or tenant isolation; this mode is only for the single Vercel account owner.

| Phase 9.2 check | Result |
|---|---|
| Vercel deployment configuration JSON parse | **PASS**, frontend/backend services and 9 route rules parse. |
| Backend access/config/WebSocket guard tests | **PASS**, 27 targeted tests. |
| `npm run lint` | **PASS**, exit 0. |
| `npm run build` | **PASS**, optimized production build and TypeScript validation completed. |
| Separate `npm run typecheck` | **INTERRUPTED**, Next route type generation completed, but the standalone checker stalled; the build's TypeScript validation passed. |
| Vercel project protection configured to All Deployments | **BLOCKED**, Vercel deployment tool unavailable in this session. |
| Protected preview, candles and live WebSocket | **NOT RUN**, no deployment exists. |

## Phase 9 initial release decision (historical baseline; superseded above)

**BLOCKED for live-market release.** Automated application and analytical tests pass, and the UI correctly reports the feed as disconnected when it receives no provider data. Current Biquote history requests return HTTP 503, no stream is connected, and there is no real-provider evidence. Public deployment is also blocked because the backend accepts application traffic only from loopback and has no authentication or user isolation. The software can be used as a local analysis preview when a provider is reachable. This decision is about acceptance evidence and availability; it makes no claim about trading performance.

## Acceptance plan and execution matrix

Status meanings: **PASS** = executed with the stated evidence; **BLOCKED** = requires live access/infrastructure that was unavailable; **N/A** = feature not implemented or not applicable.

| ID | Scenario and acceptance | Result | Evidence / actual outcome |
|---|---|---|---|
| UAT-001 | Backend starts and responds to liveness | PASS | Local backend on port 8001 answered `/health/live`; overall health reports process OK. |
| UAT-002 | Dashboard loads and renders chart shell | PASS | Playwright desktop dashboard test; direct preview returned HTTP 200 and title `Trading Analysis Console`. |
| UAT-003 | Browser console and requests are clean | BLOCKED | Diagnostic observed one console 503 for the candle request to port 8001. One request was aborted during page initialization; the other returned 503. No JavaScript page exception was observed. Cause is the unavailable provider response, not an unhandled UI exception. |
| UAT-004 | Desktop layout does not overflow | PASS | Direct preview at 1280×800: document width equals viewport; desktop E2E also passes. |
| UAT-005 | Mobile dashboard remains usable without horizontal overflow | PASS | Playwright mobile scenario confirms sidebar collapse, chart usability, and no horizontal overflow. |
| UAT-006 | Instrument switch refreshes API, stream, label, and price | PASS (mock) | E2E scenario verifies requests/socket use the selected instrument and previous market price is replaced. No real quotes asserted. |
| UAT-007 | Every UI instrument/timeframe maps to provider symbol and interval | PASS (mock) / BLOCKED (provider) | UI declares 6 instruments (XAUUSD, USDJPY, NASDAQ, US30, EURUSD, GBPUSD) and 8 timeframes (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w); E2E covers instrument and timeframe switching. Real symbol support, candle values, and all 48 provider combinations could not be confirmed because Biquote returned 503. |
| UAT-008 | Unsupported symbols fail clearly and do not fall back | PASS (unit coverage) | Provider/API tests cover mapping and unsupported symbol behavior; no actual provider request could establish upstream behavior. |
| UAT-009 | Live stream updates chart and recovers after disconnect | PASS (mock) / BLOCKED (live) | E2E feeds a mocked candle, simulates WebSocket drop, checks stale/reconnecting state and backfill recovery. Live stream has zero active feeds and was not observed. |
| UAT-010 | Candle ordering, timestamps, closed-candle analysis | PASS (deterministic tests) | Backend candle/analysis tests pass; browser fixtures are timestamp ordered. Real-provider OHLC comparison is blocked. |
| UAT-011 | Swing labels, BOS/CHoCH, zones, trend and MTF annotations align with API | PASS (deterministic/mock) | Backend analysis suite and E2E analysis/overlay tests pass; overlay pixel check passes. No real feed data available. |
| UAT-012 | Instrument/timeframe change removes stale annotations | PASS (mock) | E2E test switches market and timeframe, verifies re-analysis, and checks old market result is absent. |
| UAT-013 | LONG/SHORT setup math, invalidation, expiry, duplicates and no-signal cases | PASS (backend suite) | 139 backend tests pass, including setup lifecycle and invalid/stale/insufficient-data paths. No broker order is placed; setup plans remain analysis only. |
| UAT-014 | Setup history and persistence are truthful | PASS (failure-safe code tests) / BLOCKED (database) | Optional persistence is surfaced as unavailable when not configured. Current health reports `configuration_incomplete`; Supabase access and restart persistence could not be tested. |
| UAT-015 | Backtest timing, costs, same-bar exits, metrics, splits and determinism | PASS (backend suite + fixture UI) | Backend tests pass; E2E submits a deterministic fixture response and renders split, curve, and ledger. UI fixture result is not market performance evidence. No independent hand-calculated metric acceptance case was run during this phase. |
| UAT-016 | Health distinguishes process, provider, freshness and database | PASS | `/health` returned process OK, provider configured, `market_data=not_observed`, `data_freshness=unknown`, and `database=configuration_incomplete`; candle endpoint returned 503. |
| UAT-017 | Missing/invalid configuration and unsafe production settings fail safely | PASS (automated) | Configuration tests are included in the 139 passing backend tests. Secret values were not emitted in this report. |
| UAT-018 | Invalid requests, exceptions and request IDs are handled safely | PASS (automated) | API/health/request-ID tests pass within the backend suite. |
| UAT-019 | Logs and client bundles expose no configured credentials | PASS (static scan) | Phase 8 secret-hygiene scan found no configured secret values or credential names in 47 static assets; only example env files are tracked. This does not replace a hosted deployment scan. |
| UAT-020 | Dependency advisories are checked | BLOCKED | `npm audit --omit=dev` could not reach the npm advisory endpoint. `pip-audit` is not installed in this environment. No clean vulnerability result is claimed. |
| UAT-021 | Authentication and tenant isolation | N/A (not implemented) | No login, user identities, or per-user authorization exist. Do not deploy as a public multi-user app. |
| UAT-022 | Deployment, production configuration and rollback | BLOCKED | No deployment project/credentials or authorized deployment target was available. No production deploy, domain change, or database mutation was performed. |
| UAT-023 | Recovery and CI workflow | BLOCKED (hosted CI) | Local Playwright recovery simulation passes. GitHub Actions and the disposable PostgreSQL migration job have not run on GitHub. |

## Baseline commands and results

Commands were executed before any Phase 9 source edits. The only Phase 9 deliverable is this report; no application code change was needed to address the tested UI scenarios.

| Command | Result |
|---|---|
| `backend\.venv\Scripts\python.exe -m pytest -q` (from `backend`) | **139 passed**, 1 upstream Starlette/AnyIO deprecation warning, 17.92s. Initial `python` launcher attempt failed because Windows Store Python alias was unavailable; rerun used the project virtual environment. |
| `npm test` | **39 passed**, 4 test files, 30.40s. Initial sandbox run was denied access loading the test config; elevated rerun succeeded. |
| `npm run e2e` | **14 passed**, 2.6m. Uses mocked API/WebSocket routes and an isolated Next build, so it validates frontend journeys but not real market data. |
| `npm run lint` | **PASS**, exit code 0. |
| `npm run typecheck` | **PASS**, route type generation and TypeScript check completed. Re-run after production build to normalize generated Next types. |
| `$env:NEXT_DIST_DIR='.next-phase9-build'; npm run build` | **PASS**, optimized production build completed and statically generated `/` and `/_not-found`. |
| `npm audit --omit=dev` | **BLOCKED**, npm advisory endpoint unavailable. |

The first frontend test attempt failed for sandbox filesystem access. The same unmodified test suite then passed when run with the required access. The Windows `python` launcher was missing; project venv invocation passed. These environment errors were not suppressed or counted as test passes.

## Implemented capability audit

- **Implemented:** Biquote and optional OANDA provider adapters; historical candle endpoint; WebSocket feed/reconnect/backfill code; six instruments and eight timeframes; candlestick display, structure/zone/trendline overlays; closed-candle structure analysis and MTF context; rule-based trade setup generation and lifecycle; bounded historical backtest with cost inputs, ledger, metrics, and chronological splits; liveness/readiness and data-freshness reporting; optional Supabase-backed history.
- **Not available in this environment:** a reachable live provider feed, usable OANDA credentials, configured Supabase connection, production deployment, hosted CI result, authenticated users, and multi-user isolation.
- **Safety boundary:** no order placement or broker-account mutation was found or invoked. Backtests and setup plans are analytical outputs, not fills or profitability evidence.

## Defects and release blockers

| ID | Severity | Finding / reproduction | Root cause | Fix / retest |
|---|---|---|---|---|
| BLK-001 | High | Load preview; `/api/candles?symbol=XAUUSD&tf=15m` returns HTTP 503; chart says Disconnected and no data. | Biquote is configured but not producing candle data; exact upstream cause is unknown from the available response. Backend health confirms no observed market data and zero active streams. | No safe application change can establish upstream availability. The UI correctly reports unavailable data and does not substitute synthetic/live-looking prices. Retest blocked until provider returns valid candles and stream status. |
| BLK-002 | Critical for public release | Any non-loopback API/WebSocket use is rejected; no authentication or user isolation exists. | Intentional local-only server boundary. | No code change in Phase 9. Keep deployment blocked until an identity-aware access boundary and tenant authorization are designed, implemented, and tested. |
| BLK-003 | Medium release evidence | Dependency advisory scan failed. | npm registry advisory endpoint was unreachable; Python audit utility absent. | Retry both audits in a network-enabled CI environment and review results before release. |
| BLK-004 | Medium release evidence | Supabase persistence and migration were not exercised against a real database. | Database configuration incomplete and no disposable PostgreSQL service available locally. | Run CI migration job and a non-production persistence/restart acceptance test. |

No Critical/High application logic defect was discovered in the executed deterministic and mocked acceptance suite. BLK-002 is a release blocker by design rather than a newly introduced code regression.

## Recommended next actions

1. Restore/verify provider access and repeat candle plus streaming checks for each advertised instrument/timeframe; compare selected OHLC bars directly against the provider response.
2. Run dependency audits and the SQL migration workflow in connected CI; resolve any findings before release.
3. Configure and verify a non-production Supabase instance if persistence is required.
4. Before public hosting, implement authentication, authorization, per-user data isolation, trusted proxy/network boundaries, and shared abuse controls; then conduct deployment smoke and rollback tests.

## 2026-10-23 Vercel deployment follow-up

**Overall public release: BLOCKED.** The user's request to deploy this project publicly was carried out, but the absence of authentication and user isolation remains a release blocker. The public endpoint is an analysis-only demo and is not suitable for private data or multi-user use.

### Deployment evidence

- **Production URL:** https://trading-analysis-console.vercel.app
- **Vercel project:** `sir-henery/trading-analysis-console`
- **Deployment:** `dpl_62SHmPrrkSLw1u2BT3d1y2qx8otj`, status `READY`, production target, build duration 1m 13s. Vercel built the Next.js frontend and FastAPI backend as separate services. The service framework selectors and service rewrite patterns were made explicit after the first build attempt exposed ambiguous framework detection and a root route mismatch.
- **Public access:** project SSO protection is disabled; the production URL returned HTTP 200 without sign-in. No broker credentials or Supabase settings were configured. The database is not configured, and no order placement is present. Public HTTP API and backtest endpoints are reachable; Vercel usage and per-process limits can be consumed by anonymous visitors.
- **Upload hygiene:** `.vercelignore` excludes local environment files, Python virtual environments, test caches, and the temporary npm CLI cache. No credentials were printed or intentionally uploaded.

### Hosted smoke checks

| Check | Result | Evidence |
|---|---|---|
| Homepage | PASS | `GET /` returned HTTP 200 and Next.js HTML. |
| Process liveness | PASS | `GET /health/live` returned HTTP 200. |
| Provider health | PASS WITH LIMITATIONS | `GET /health` returned provider `biquote`, configuration `configured`, but market data `not_observed` and freshness `unknown` before a stream connected. |
| Historical candles | PASS | `GET /api/candles?symbol=XAUUSD&tf=15m` returned HTTP 200, 193 ordered candles from Biquote; provider HTTP log was 200. |
| Live analysis and setup API | PASS WITH LIMITATIONS | `/api/analysis/XAUUSD?timeframe=15m` and `/api/setups/XAUUSD?timeframe=15m` both returned HTTP 200 with `status=ok` and fresh data at the check. No setup met the signal rules, so the API returned zero setups rather than inventing an entry/target. `/health/ready` returned ready while explicitly stating live data is not observed until a feed connects. |
| Hosted WebSocket | PASS WITH LIMITATIONS | A real `wss://` session established, received a 50-candle backfill, reported `upstream=connected` and `stale=false`, and delivered 23 changing candle-close updates in 12 seconds. The provider status also reported `market_open=false`; the session does not establish open-market freshness. A browser E2E route was not run against production. |
| Authentication and user isolation | BLOCKED | Vercel SSO was disabled to honor the public-access request. The app has no general authentication, user authorization, tenant isolation, or shared abuse controls. Do not use it for private or multi-user information. |
| Rollback | NOT TESTED | The production alias is live; no rollback was performed. |

### Final assessment

The Vercel deployment itself is **PASS** for availability of the homepage and historical candle API. Hosted streaming is **PASS WITH LIMITATIONS**: connection, backfill, and changing updates were observed, but market-open freshness remains unproven because the provider reported the instrument market closed during the check. Public release readiness remains **BLOCKED** by the missing access control and user/data isolation described above. This deployment is not a profitability claim and is not trading advice.

