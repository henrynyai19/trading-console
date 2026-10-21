# Trading Analysis Console: Architecture

Decision-support only. No order execution anywhere in the codebase.

## Services
- frontend (Next.js, Vercel): UI, chart rendering, Zustand state. Talks only to our backend. Holds no secrets.
- backend (FastAPI): market-data providers, analysis engine, signal engine, REST + WebSocket.
- Supabase Postgres: signals, backtest runs, cached candles (UTC timestamps).

## Backend modules (Phase 1 ships the first two)
- providers/: `MarketDataProvider` interface + symbol map. One adapter per vendor.
- api: /health, /api/instruments, /api/candles, later /ws/{symbol}/{tf}
- analysis/ (Phase 3): swings, S/R zones, structure (HH/HL/LH/LL/BOS/CHoCH), trend, MTF
- signals/ (Phase 4): setups, SL/TP, scoring, state machine
- backtest/ (Phase 5)

## Data-provider options
| Provider | Covers | Notes |
|---|---|---|
| OANDA v20 | FX, XAU, US100, US30 CFDs | REST + streaming, practice account is free, CFD pricing |
| Twelve Data | FX, metals, indices | REST + WebSocket, indices need a paid tier |
| Polygon.io | US indices/futures, FX | Strong US coverage, paid for real time |
| Dukascopy / IC Markets via cTrader | FX, CFDs | Good history, heavier integration |

Recommendation: OANDA v20. One vendor covers all six instruments with candles and streaming.
NASDAQ and US30 are CFDs there (NAS100_USD, US30_USD), so prices differ from cash indices or futures. This must be labelled in the UI.

## Credentials
OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_ENV (practice|live), Supabase URL + service key (backend only).

## Risks
1. Index symbol ambiguity (CFD vs futures vs cash), so mapping is explicit and shown.
2. Repainting: swings are confirmed only after N right-hand bars; signals use closed candles only.
3. Free-tier rate limits and WebSocket drops need backoff and gap backfill.
4. Backtest bias: spreads, slippage, no lookahead, out-of-sample split.
5. Hosting: Python with TA-Lib needs a container host (Fly.io, Railway or Render), not Vercel.

## Phases
1 Foundation (this drop) | 2 Real data | 3 Analysis engine | 4 Trade setups | 5 Backtest + tests + deploy


---
# Phase 2 additions

## Data flow
```
OANDA REST (official candles) ─┐                         ┌─ REST /api/candles ──► initial history
                               ├► MarketDataService ─────┤
Supabase cache (optional) ─────┘   (cache + gap backfill)└─ FeedHub ── WS /ws ──► backfill, candle, reconcile, status
OANDA pricing stream (sampled) ───────────────────────────► FeedHub (one upstream stream per symbol, ref-counted)
```
- **Official candles are authoritative.** Stream prices only extend the developing candle. A candle that closes by the clock is
  marked `provisional` until an official candle arrives (on rollover and every 60 s); official data then replaces it and
  provisional buckets that match no official candle are dropped (`reconcile.drop`).
- **Rank rule** (backend `merge_candles`, frontend `mergeCandles`): official closed > official developing > provisional. A lower rank never overwrites a higher one, and candles are keyed by UTC time, so duplicates cannot occur.
- **Resilience**: heartbeat timeout 15 s, reconnect backoff 1,2,4...30 s (+jitter), backfill from the last official candle after every reconnect, HTTP 429 honours `Retry-After`, 5xx and network errors retry 4 times, then the API answers 503 and the UI shows DATA UNAVAILABLE.
- **Cache** (`SupabaseCache`, PostgREST over httpx): complete official candles only; unique `(instrument, timeframe, ts)`; tail fetch when fresh, full refetch when stale; weekend-aware gap detection with a one-time gap fill; any Supabase error disables the cache for 60 s and the app continues on OANDA alone.
- **Credentials** live only in backend env vars, are excluded from `repr`, error messages and responses (tested).

## Phase 3 hooks
- `FeedState` and the merged candle list are the single source of closed candles for analysis; analysis must use `complete and not provisional` only.
- Planned modules: `analysis/swings.py`, `zones.py`, `structure.py`, `trend.py`, `mtf.py`, exposed as `/api/analysis?symbol&tf` and an `analysis` WS message type; the frontend adds an annotation layer on `PriceChart` (price lines and series markers) driven from the store.
- Multi-timeframe analysis will reuse `MarketDataService.history` per higher timeframe.


---
# Phase 3: analysis engine

```
MarketDataService.history (OANDA/cache) ─► closed_candles() [complete & official only]
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
