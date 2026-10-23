create table if not exists public.backtest_runs (
    id uuid primary key,
    instrument text not null,
    timeframe text not null,
    strategy_version text not null,
    data_source text not null,
    start_time bigint not null,
    end_time bigint not null,
    created_at timestamptz not null default now(),
    payload jsonb not null
);

create index if not exists backtest_runs_created_at_idx on public.backtest_runs (created_at desc);
create index if not exists backtest_runs_market_idx on public.backtest_runs (instrument, timeframe, created_at desc);

alter table public.backtest_runs enable row level security;
revoke all on public.backtest_runs from anon, authenticated;
grant all on public.backtest_runs to service_role;

comment on table public.backtest_runs is
    'Reproducible historical simulation payloads and trade ledger; never broker orders or account positions.';
