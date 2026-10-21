# Trading Analysis Console (Phase 3: automated technical analysis)

Decision-support only. It never places trades and never shows simulated prices as live data.

## Setup
1. **OANDA practice account**: create a free fxTrade Practice account, then generate a personal API token in the OANDA hub. Note the account ID (looks like `101-001-1234567-001`).
2. **Backend**
   ```bash
   cd backend
   cp .env.example .env        # fill in OANDA_API_KEY and OANDA_ACCOUNT_ID yourself; never paste keys into chats or commits
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```
3. **Frontend**
   ```bash
   cd frontend
   cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
   npm install && npm run dev   # http://localhost:3000
   ```
4. **Optional Supabase cache**: run `backend/supabase/migrations/20261018000000_candles.sql` in the Supabase SQL editor, then set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (service-role key, backend only). Leave them empty to use OANDA directly.
5. Open `/health` to confirm `"provider": "oanda"`, then `/api/instruments` to see which of the six instruments your account offers.

Without credentials the dashboard shows **DATA UNAVAILABLE**.

## Tests
| Suite | Command |
|---|---|
| Backend unit + WebSocket integration (mocked OANDA) | `cd backend && pytest` |
| Frontend type check / lint / build | `npm run typecheck && npm run lint && npm run build` |
| Frontend unit tests | `npm test` |
| Browser tests (Playwright, mocked backend) | `npx playwright install chromium && npm run e2e` |
| Real-HTTP integration against the local mock OANDA | start `scripts/mock_oanda.py` on :8101, start the backend with `OANDA_REST_URL`/`OANDA_STREAM_URL` pointing at it, run `python scripts/integration_check.py` |
| Analysis integration (real HTTP, mock OANDA): alignment of every annotation with real candles, no-lookahead timestamps, MTF hierarchy | same `integration_check.py` (49 checks) |
| **Live OANDA verification** | See `PHASE2_REPORT.md`, "Still required". Not done yet. |

## Analysis (Phase 3)
`GET /api/analysis/{instrument}?timeframe=15m` (aliases `M15`, `H1`, `H4`, `D`, `W` also accepted; optional `left`, `right`, `min_swing_strength`, `include_invalidated`).
Returns trend + score + explanation, swings (HH/HL/LH/LL), support/resistance zones, BOS/CHoCH events, trendlines, multi-timeframe bias, freshness and deterministic commentary.
`status` is `ok`, `insufficient_data` or `stale`; for the last two **no analysis is returned**. All inputs are official closed candles; the developing candle is never used.
The dashboard right panel shows it, and the *Chart layers* checkboxes toggle zones, swing labels, BOS/CHoCH and trendlines.

## API
- `GET /health`, `GET /api/instruments` (availability per account), `GET /api/candles?symbol&tf&count`
- `WS /ws?symbol=&tf=`: messages `backfill`, `candle`, `reconcile` (with `drop`), `status`, `error`

See `ARCHITECTURE.md` for design and `PHASE2_REPORT.md` for results and limitations.
