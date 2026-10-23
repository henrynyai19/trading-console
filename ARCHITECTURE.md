# Trading Analysis Console: Architecture

Decision-support only. No order execution anywhere in the codebase.

## Services
- frontend (Next.js): UI, chart rendering, Zustand state. Talks only to our backend. Holds no secrets. Local API defaults to `127.0.0.1`; Vercel uses a same-origin service route.
- backend (FastAPI): market-data providers, analysis engine, analytical setup engine, REST + WebSocket. Local requests are loopback-only. Hosted requests are accepted only in Vercel Functions, and rely on project-wide Vercel Authentication being configured for all deployments before ingress. Keep project access limited to the single user; no multi-user account or data-isolation model exists.
- Supabase Postgres: cached candles and analytical setup history (UTC timestamps). Backtest runs are returned by the API and are not currently persisted.

## Backend modules (Phase 1 ships the first two)
- providers/: `MarketDataProvider` interface + symbol map. One adapter per vendor.
- api: /health, /api/instruments, /api/candles, later /ws/{symbol}/{tf}
- analysis/ (Phase 3): swings, S/R zones, structure (HH/HL/LH/LL/BOS/CHoCH), trend, MTF
- `app/setups.py` (Phase 5): explainable setup quality, closed-candle directional confirmation, ATR-buffered structural stops, R targets, higher-timeframe/opposing-zone context and lifecycle replay. Setups are decision support only and never place orders.
- `app/backtesting.py`: closed-candle BOS/CHoCH replay, next-candle entries, explicit spread/slippage inputs and conservative same-candle barrier ordering.
- `app/backtesting.py` (Phase 6): bounded UTC date-range replay with candle validation, warm-up, per-side cost assumptions, normalized R ledger, MFE/MAE, equity/drawdown, group analytics and chronological evaluation splits.
- `app/setup_store.py`: optional PostgREST snapshots of setup lifecycle in Supabase.

## Data-provider options
| Provider | Covers | Notes |
|---|---|---|
| Biquote | FX, gold, index CFDs | Default; public REST candles + SignalR streaming, no account or API key. Historical candles may combine Yahoo bootstrap, tick aggregation and MT5 history. |
| OANDA v20 | FX, XAU, US100, US30 CFDs | Optional REST + streaming; requires OANDA account credentials, practice account available |
| Twelve Data | FX, metals, indices | REST + WebSocket, indices need a paid tier |
| Polygon.io | US indices/futures, FX | Strong US coverage, paid for real time |
| Dukascopy / IC Markets via cTrader | FX, CFDs | Good history, heavier integration |

The app defaults to Biquote for public candles and pushed prices. OANDA remains available by setting `MARKET_DATA_PROVIDER=oanda` and configuring its credentials. Provider symbols appear in the UI. NASDAQ and US30 map to Biquote's USTEC and US30 CFDs (and to OANDA's NAS100_USD and US30_USD); CFD prices can differ from cash indices or futures.

## Credentials
No credentials are needed for the default Biquote read feed. OANDA_API_KEY, OANDA_ACCOUNT_ID and OANDA_ENV (practice|live) are required only when OANDA is selected. Supabase URL + service key are optional and backend-only.

## Risks
1. Index symbol ambiguity (CFD vs futures vs cash), so mapping is explicit and shown.
2. Repainting: swings are confirmed only after N right-hand bars; signals use closed candles only.
3. Free-tier rate limits and WebSocket drops need backoff and gap backfill.
4. Backtest bias: spreads, slippage, no lookahead, out-of-sample split.
5. Runtime: the live feed needs a persistent ASGI process with outbound provider streaming and an inbound client WebSocket. This version binds locally; hosted access is unsupported until authentication, authorization, data isolation and trusted proxy policy are implemented.

## Phases
1 Foundation | 2 Real data | 3 Analysis engine | 4 Trade setup foundation | 5 Trade setup lifecycle, risk levels and context

Phase 5 provides rule-based analytical plans and lifecycle replay. Backtesting is a separate historical simulation feature. Neither opens, manages or closes broker positions. Provider behavior should be checked against live data; synthetic mock integration checks remain separate.

## Phase 6 backtesting
- `POST /api/backtests` accepts UTC epoch start/end, instrument, timeframe, risk target, cost assumptions and chronological split percentages. It requests provider history with up to 100 warm-up candles, evaluates only the requested range, and caps the range at 4,900 bars. The existing `GET /api/backtest/{instrument}` count-based endpoint remains compatible.
- Signals come from the existing structure analyzer. Swings have explicit `confirmed_idx`; the simulator only uses an opposing swing confirmed before the signal candle. Signals are handled in candle order and entries occur at the following candle open. No optimizer sees the holdout period; the same submitted settings are replayed in every split.
- Since the available history is mid OHLC, spread, slippage and commission are assumptions in ticks per side, charged once per entry and exit. Stop wins same-bar ambiguity; a gap through a stop exits at the worse open. No money or position-size calculation is produced without contract metadata.
- The synchronous run is bounded by candle count and returns trades, equity/drawdown points and summaries in one response. With optional Supabase configured, `SupabaseBacktestStore` saves a reproducible JSON result and supports run, trade and equity retrieval; `20261023000000_backtest_runs.sql` adds a service-role-only table. Without Supabase the response flags the result as unsaved. A background job store does not exist; the frontend exposes loading, completed and error states for these bounded runs.

## Phase 8 operations and release boundary
- `GET /health/live` is a minimal public process probe; `GET /health/ready` checks that a supported provider is configured. Neither claims live market data or database connectivity. `GET /health` provides local-only status for configuration, observed feed freshness and whether Supabase is configured.
- Backend logs use JSON with UTC timestamps and allowlisted fields. HTTP requests receive an `X-Request-ID`; request IDs flow into persistence/backtest log events. Request bodies, credentials, prices, and account identifiers are not logged.
- Startup validates environment selection, OANDA credentials when selected, paired Supabase settings and HTTPS production origins. `backend/.env.production.example` and root `.env.production.example` contain templates only.
- Setup confirmation snapshots are protected from replacement by migration `20261024000000_immutable_confirmed_setup_snapshot.sql`. Setup lifecycle history and completed backtests persist only with Supabase migrations and credentials; live feed state and in-flight backtests are in process and reset on restart.
- `.github/workflows/ci.yml` gates quality checks on pushes and pull requests. It runs Python/Node tests, lint, type check, production build, Playwright, and dependency advisory checks.
- There is no login, per-user data ownership, or authenticated WebSocket access. The local peer guard is intentional; do not publish the backend until an identity provider, proxy trust policy, and shared abuse limits are implemented and verified. No production deployment has been performed.
- Vercel deployment configuration is defined in `vercel.json` using Vercel Services (beta): Next.js serves the dashboard and FastAPI serves `/api/*`, `/health*` and `/ws` on the same protected origin. Vercel Authentication must be enabled for All Deployments so access checks run before either service receives public traffic. Live stream lifetime is bounded by the Vercel Function maximum duration; the browser reconnects and backfills after the socket closes. Verify provider access and stream behavior on the deployed preview.


---
# Phase 2 additions

## Data flow
```
Biquote REST candles ──────────┐                         ┌─ REST /api/candles ──► initial history
                               ├► MarketDataService ─────┤
OANDA REST (optional) ─────────┘                         └─ FeedHub ── WS /ws ──► backfill, candle, reconcile, status
Biquote SignalR / OANDA stream ───────────────────────────► FeedHub (one upstream stream per symbol, ref-counted)
```
- **Official candles are authoritative.** Stream prices only extend the developing candle. A candle that closes by the clock is
  marked `provisional` until an official candle arrives (on rollover and every 60 s); official data then replaces it and
  provisional buckets that match no official candle are dropped (`reconcile.drop`).
- **Rank rule** (backend `merge_candles`, frontend `mergeCandles`): official closed > official developing > provisional. A lower rank never overwrites a higher one, and candles are keyed by UTC time, so duplicates cannot occur.
- **Resilience**: heartbeat timeout 15 s, reconnect backoff 1,2,4...30 s (+jitter), backfill from the last official candle after every reconnect, HTTP 429 honours `Retry-After`, 5xx and network errors retry 4 times, then the API answers 503 and the UI shows DATA UNAVAILABLE.
- **Cache** (`SupabaseCache`, PostgREST over httpx): only enabled for providers configured to share its provider-specific candle history. Biquote candles bypass it to avoid mixing providers. Any Supabase error disables the cache for 60 s and the app continues with the selected provider.
- **Credentials** live only in backend env vars, are excluded from `repr`, error messages and responses (tested).

## Phase 3 hooks
- `FeedState` and the merged candle list are the single source of closed candles for analysis; analysis must use `complete and not provisional` only.
- Planned modules: `analysis/swings.py`, `zones.py`, `structure.py`, `trend.py`, `mtf.py`, exposed as `/api/analysis?symbol&tf` and an `analysis` WS message type; the frontend adds an annotation layer on `PriceChart` (price lines and series markers) driven from the store.
- Multi-timeframe analysis will reuse `MarketDataService.history` per higher timeframe.


---
# Phase 3: analysis engine

```
MarketDataService.history (selected provider) ─► closed_candles() [complete & provider-backed only]
   └► swing_detector ─► structure_analyzer (BOS/CHoCH) ─┐
                      ├► support_resistance (zones)      ├► AnalysisService ─► /api/analysis/{instrument}
                      ├► trendline_detector              │      (+ MTF: same pipeline on 1-2 higher timeframes)
                      └► trend_detector ─────────────────┘
backend/app/analysis/: indicators, models, swing_detector, structure_analyzer, support_resistance, trendline_detector,
trend_detector, multi_timeframe, commentary, analysis_service
```
## Non-repainting rules (verified by replay tests)
- A pivot at candle i is confirmed when candle i+`right` closes (`confirmed_at` = that close). Swing strength and labels use only data up to that candle, so a confirmed swing never changes. Test: for every prefix k, `swings(candles[:k]) == [s for s in swings(all) if s.confirmed_idx <= k-1]`.
- Structure is evaluated candle by candle; a swing is usable from the candle after its confirmation; events fire on a **close** beyond the level. Test: `events(candles[:k]) == [e for e in events(all) if e.break_idx <= k-1]` for every k. `confirmed_at` of an event is the close of the break candle.
- **Rules**: seed trend silently on the first close beyond the latest swing; BOS = close beyond the latest unbroken swing of the trend direction formed after the last event; CHoCH = close beyond the protected swing (the pullback low/high that supported the last break, tightened by higher lows/lower highs). Each level breaks once; one event per candle.
- **Zones are live objects.** They are recomputed from confirmed swings each time: boundaries/strength can change as swings join a cluster, and role (support<->resistance) flips on a close through the zone; a second flip invalidates it. Swings and events never change.
## Scores
Trend score (-100..100) = structure 40 + event 15 + moving averages 25 + persistence 10 + momentum 15 (capped). Zone strength (0..100) = touches 40 + swing prominence 20 + recency 20 + role-reversal bonus 10 - width penalty. Both are rule-based quality measures, **not probabilities**.
## Freshness / caching
Stale = latest closed candle older than 3 timeframe-lengths of *trading* time (weekend closure removed); weekends report `market_closed`. Stale or insufficient (<60 closed candles) data returns no analysis. Results are cached per (instrument, timeframe, config, last closed candle time, count): a newly completed candle invalidates it; provider fetches are memoised for 5 s.
## Frontend
`AnnotationLayer` owns all drawings: swing/BOS/CHoCH markers (series markers), break levels and trendlines (line series), zones (a canvas series primitive that recomputes coordinates on every paint). It updates only when the analysis, the layer toggles or the charted range change, never on ticks. The page refetches analysis only when the last official closed candle changes (150 ms debounce).
## Phase 4 hooks
Zones, swings, protected levels and ATR are available per timeframe for entry/stop/target logic; `StructureResult.protected_level` is the natural structural stop reference.
