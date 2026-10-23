-- Once a setup has a confirmation snapshot, lifecycle updates must not rewrite it.
-- This is an additive guard and leaves event/status updates available to the service role.
create or replace function public.preserve_confirmed_setup_snapshot()
returns trigger
language plpgsql
as $$
declare
    old_snapshot jsonb;
    new_snapshot jsonb;
begin
    old_snapshot := old.payload -> 'confirmed_snapshot';
    new_snapshot := new.payload -> 'confirmed_snapshot';

    if old_snapshot is not null
       and old_snapshot <> 'null'::jsonb
       and new_snapshot is distinct from old_snapshot then
        new.payload := jsonb_set(
            coalesce(new.payload, '{}'::jsonb),
            '{confirmed_snapshot}',
            old_snapshot,
            true
        );
    end if;

    return new;
end;
$$;

drop trigger if exists trade_setups_preserve_confirmed_snapshot on public.trade_setups;
create trigger trade_setups_preserve_confirmed_snapshot
before update of payload on public.trade_setups
for each row execute function public.preserve_confirmed_setup_snapshot();
