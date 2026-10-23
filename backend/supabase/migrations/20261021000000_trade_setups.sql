create table if not exists public.trade_setups (
    id text primary key,
    instrument text not null,
    timeframe text not null,
    status text not null check (status in ('developing', 'awaiting_confirmation', 'confirmed', 'active_reference', 'entry_touched', 'target_1_reached', 'target_2_reached', 'target_3_reached', 'stopped_out', 'invalidated', 'expired', 'active', 'target_touched')),
    created_at timestamptz not null,
    updated_at timestamptz not null default now(),
    payload jsonb not null
);

create index if not exists trade_setups_instrument_tf_created_idx
    on public.trade_setups (instrument, timeframe, created_at desc);

alter table public.trade_setups enable row level security;

comment on table public.trade_setups is
    'Analytical setup snapshots only; these rows never represent broker orders or positions.';
