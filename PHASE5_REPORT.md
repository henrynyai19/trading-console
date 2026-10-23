# Phase 5 — Trade Setup Engine, Risk Management and Lifecycle

## Delivered

- Setup candidates are derived from fresh, provider-backed, completed candles. Provisional candles cannot confirm a setup.
- A setup requires a directional candle close through the planned confirmation level before it becomes confirmed. The API records the confirmation candle and a snapshot of the levels at confirmation.
- Structural stops include a configurable ATR buffer (default 0.25 ATR). Targets are expressed as 1.5R, 2R and 3R from the planned entry and stop distance.
- The setup response includes higher-timeframe alignment/conflicts, relevant opposing-zone context, cautions, invalidation and expiry details, and the timestamp of the latest processed candle.
- Lifecycle replay progresses through confirmation, reference activation, entry-zone touch, target milestones, stop, invalidation and expiry. Entry touches are not represented as broker fills. Stop wins when one candle spans both stop and target; terminal states never reopen.
- The setup API catches up over every newly closed candle since its saved lifecycle point, and keeps lifecycle history when available. Supabase's new additive migration allows the expanded states on existing databases.
- The chart can display a selected setup's entry, stop and target levels. The setup panel explains that the levels are analytical references and that monetary risk/position size cannot be calculated without account equity and instrument contract details.
- README and architecture notes now describe the current lifecycle and its limits.

## Validation

- Preflight baseline: the backend suite initially had 6 failures tied to legacy OANDA/fake-provider source expectations and the no-provider WebSocket error contract. The fixture/contract was aligned with the current provider-agnostic behavior; the final full suite passed. Initial frontend execution was also blocked by the sandbox's esbuild worker permissions, so it was rerun in the permitted test environment.
- Backend: `120 passed` (`python -m pytest -q`). One upstream Starlette/AnyIO deprecation warning.
- Frontend unit tests: `39 passed` (`npm test -- --pool=threads --maxWorkers=1 --reporter=dot`). React emitted an existing `act(...)` warning in the reconcile test.
- TypeScript: `npm run typecheck` passed.
- ESLint: `npm run lint` passed.
- Production build: `npm run build` passed.
- Browser suite: all 13 Playwright checks reported `ok`, including live chart rendering, connection recovery, analysis overlays and mobile layout. The Playwright runner did not exit after the final test, so the process was interrupted during cleanup; no test failure was reported. These tests use mocked market data and do not establish live-provider availability.

## Database setup

When Supabase setup history is enabled, apply migrations in order from `backend/supabase/migrations/`, including `20261022000000_setup_lifecycle_states.sql`. Without Supabase, setups still run, but saved history is unavailable.

## Risk limits

These are rule-based decision-support levels, not a probability of success or financial advice. An entry-zone touch does not prove a fill. Risk is currently measured in price units and R; the application does not calculate account-currency exposure or position size and does not submit orders. Market-data freshness and quality depend on the selected feed.

## Run locally

Follow the setup steps in `README.md`. Start the FastAPI service from `backend/` on port 8000 and the Next.js app from the repository root on port 3000. The default Biquote market-data provider requires no account credentials; availability depends on the upstream public feed.

## Manual OANDA verification

OANDA was not configured for this run, so real OANDA-specific Phase 5 behavior remains unverified. To check it, put `MARKET_DATA_PROVIDER=oanda`, `OANDA_API_KEY`, `OANDA_ACCOUNT_ID` and `OANDA_ENV=practice` in `backend/.env`, restart the backend, open the dashboard, and confirm the OANDA instrument/provider label and candle timestamps. Select an instrument/timeframe with fresh closed candles and inspect `/api/setups/{instrument}?timeframe=15m`; if the rules find no setup, the response should remain empty instead of inventing one. Check that only completed directional candles confirm candidates, that displayed stop and R targets align with the returned API values, and that an entry touch is labeled reference-only. For persistence, configure Supabase, apply all migrations in order, then inspect `/api/setups/{instrument}/history?timeframe=15m` for the confirmed snapshot and lifecycle events.
