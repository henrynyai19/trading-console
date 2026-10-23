alter table public.trade_setups drop constraint if exists trade_setups_status_check;
alter table public.trade_setups add constraint trade_setups_status_check
    check (status in ('developing', 'awaiting_confirmation', 'confirmed', 'active_reference', 'entry_touched',
                      'target_1_reached', 'target_2_reached', 'target_3_reached', 'stopped_out', 'invalidated',
                      'expired', 'active', 'target_touched'));

comment on column public.trade_setups.payload is
    'Immutable confirmation snapshot plus append-only lifecycle events; analytical references only, never broker orders.';
