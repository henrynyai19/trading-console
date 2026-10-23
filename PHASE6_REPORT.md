# Phase 6 — Historical Backtesting and Strategy Validation

## Delivered

Phase 6 adds a date-range backtest workflow for the console's existing structural BOS/CHoCH strategy. The dashboard can request a bounded historical run, review aggregate metrics and chronological in-sample, validation and out-of-sample periods, inspect an equity/drawdown curve, and review the complete trade ledger. Runs are saved and retrievable when the optional Supabase store and migration are configured; otherwise the response remains available in the current page and is clearly marked unsaved.

The backend exposes `POST /api/backtests` plus saved-report, trade-ledger and equity-curve reads at `/api/backtests/{backtest_id}`, `/trades` and `/equity-curve`. The previous candle-count endpoint remains available. The dashboard adds date, target R, spread, slippage, commission and period-split controls.

## Files

- `backend/app/backtesting.py` — validated candle replay, execution assumptions, trade ledger, metrics, splits and curve.
- `backend/app/main.py` — bounded request and saved-report API routes.
- `backend/app/backtest_store.py` and `backend/app/runtime.py` — optional persistence wiring.
- `backend/supabase/migrations/20261023000000_backtest_runs.sql` — service-role-only report tables and access policy.
- `src/components/BacktestPanel.tsx`, `src/components/Panels.tsx`, `src/lib/api.ts`, `src/lib/types.ts` — dashboard and API types.
- `backend/tests/` and `tests/e2e/console.spec.ts` — unit/API and browser coverage.
- `README.md` and `ARCHITECTURE.md` — setup and design notes.

## Replay assumptions and metric definitions

- The engine uses the existing structural break/reversal logic; it does not replay the Phase 5 support/retest setup lifecycle.
- Signals are evaluated after a completed break candle. An entry is taken at the next candle's open. Confirmed swings must have been confirmed before the signal, preventing future candles from supplying swing information.
- The replay allows one position at a time. Structural stops use a 0.25 ATR buffer. The configured target is expressed in initial-risk multiples.
- Historical OHLC prices are treated as mid prices. Spread, slippage and commission are user-supplied tick assumptions charged on each side. If OHLC cannot establish whether stop or target came first, the stop is assumed first. A stop gap exits at the worse candle open.
- Only closed candles inside the requested UTC date range are evaluated; up to 100 earlier bars are used for warm-up. The request is bounded to at most 4,900 estimated bars.
- Win rate is winners divided by closed trades. Expectancy is average net R. Profit factor is positive net R divided by absolute negative net R and is undefined if there are no losses. Maximum drawdown is peak-to-trough cumulative net R. MFE/MAE are candle-range excursions in initial-risk units.
- Period splits are chronological. The same configuration is applied in each period; the engine does not optimize parameters. The out-of-sample section is descriptive and is not a significance claim or forecast.

## Validation

- Backend suite: 129 passed; one upstream Starlette/AnyIO deprecation warning.
- Frontend unit suite: 39 passed (existing React `act` warnings were observed).
- TypeScript typecheck, ESLint and production build: passed.
- Playwright browser suite: all 14 tests reported `ok`, including the date-range backtest UI. The runner did not return a clean completion result after the final test output, so this is recorded as 14 tests reported passing, with runner cleanup unconfirmed.
- Provider and browser tests use fixtures/mocked API responses for backtests. No live historical-market replay was performed.
- Supabase persistence behavior was exercised with a mocked HTTP transport. No real Supabase instance was configured, and the migration was not applied here.

## Setup and limitations

Apply all SQL files in `backend/supabase/migrations/` in order, then set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in the backend environment to enable durable report storage. Keep the service key server-side. Without that configuration, reports are not durable across page reloads.

Start the backend and frontend using the instructions in `README.md`; the local preview is `http://localhost:3000/`. No account balance, contract size, monetary P&L or broker execution is modeled. The bounded request runs inline rather than as a queued job. Historical provider candle OHLC is an approximation of executable prices. Only the structural strategy is supported; there is no strategy optimizer or walk-forward parameter fitting.
