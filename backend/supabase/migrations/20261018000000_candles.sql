-- Candle cache for the Trading Analysis Console. Timestamps are UTC candle-open times.
create table if not exists public.candles (
  id          bigint generated always as identity primary key,
  instrument  text        not null,             -- display symbol, e.g. XAUUSD
  timeframe   text        not null check (timeframe in ('1m','5m','15m','30m','1h','4h','1d','1w')),
  ts          timestamptz not null,
  open        numeric     not null check (open  > 0),
  high        numeric     not null check (high  > 0),
  low         numeric     not null check (low   > 0),
  close       numeric     not null check (close > 0),
  volume      numeric     not null default 0 check (volume >= 0),
  source      text        not null default 'oanda',
  updated_at  timestamptz not null default now(),
  constraint candles_unique unique (instrument, timeframe, ts),
  constraint candles_ohlc_ok check (high >= greatest(open, close, low) and low <= least(open, close, high))
);

-- Serves: latest N candles for an instrument/timeframe.
create index if not exists candles_lookup_idx on public.candles (instrument, timeframe, ts desc);

-- Only complete, official candles are stored. The backend uses the service-role key, which bypasses RLS;
-- with RLS on and no policies, anon/authenticated keys cannot read or write.
alter table public.candles enable row level security;
