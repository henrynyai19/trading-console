import asyncio

from app.config import Settings
from app.providers.base import Heartbeat, PriceTick, ProviderUnavailable
from app.service import MarketDataService
from app.stream import FeedHub, FeedState
from tests.helpers import BASE, FakeProvider, mk, until

SYM, TF = "EURUSD", "1m"


def tick(price, t, tradeable=True):
    return PriceTick(t, price - 0.00005, price + 0.00005, tradeable)


def test_state_extends_developing_candle_and_ignores_old_ticks():
    dev = mk(1, complete=False)
    st = FeedState(SYM, TF, [mk(0), dev])
    out = st.apply_tick(dev.high + 0.5, dev.time + 10)
    assert len(out) == 1 and out[0].high == dev.high + 0.5 and out[0].close == dev.high + 0.5 and not out[0].complete
    assert st.apply_tick(0.1, dev.time - 5) == []  # out of order: older than the developing candle
    assert st.apply_tick(dev.high + 0.5, dev.time + 11) == []  # no change, nothing to broadcast


def test_rollover_is_provisional_until_official_reconcile():
    dev = mk(1, complete=False)
    st = FeedState(SYM, TF, [mk(0), dev])
    closed, new = st.apply_tick(1.5, dev.time + 65)
    assert closed.complete and closed.provisional and new.provisional and not new.complete
    assert new.time == dev.time + 60 and st.need_reconcile
    official = [mk(1), mk(2, complete=False)]
    shown, drop = st.reconcile(official)
    assert st.candles[mk(1).time].provisional is False and st.candles[mk(1).time].complete
    assert st.candles[mk(2).time] == mk(2, complete=False) and not st.need_reconcile and drop == []


def test_reconcile_drops_misaligned_provisional_bucket_but_keeps_future_one():
    st = FeedState(SYM, TF, [mk(0, complete=False)])
    st.apply_tick(1.0, mk(0).time + 130)  # provisional bucket at +120
    bad = mk(0).time + 30
    st.candles[bad] = type(st.candles[mk(0).time])(bad, 1, 1, 1, 1, 0, False, True)
    _, drop = st.reconcile([mk(0), mk(1, complete=False)])
    assert drop == [bad] and (mk(0).time + 120) in st.candles


def make_hub(provider, **kw):
    delays = []

    async def sleep(d):
        delays.append(d)
        await asyncio.sleep(0)

    hub = FeedHub(MarketDataService(provider), provider, sleep=sleep, jitter=lambda: 0.0, **kw)
    return hub, delays


async def test_reconnects_with_exponential_backoff_then_backfills():
    prov = FakeProvider({(SYM, TF): [mk(0), mk(1, complete=False)]}, scripts=[
        [Heartbeat(1), ProviderUnavailable("drop")],      # connected, then drops          -> wait 1s
        [ProviderUnavailable("still down")],              # fails before first event       -> wait 2s
        [Heartbeat(2), tick(1.0, mk(1).time + 5), "hang"],  # recovers
    ])
    hub, delays = make_hub(prov)
    sub, tail = await hub.subscribe(SYM, TF)
    await until(lambda: prov.streams_opened == 3 and hub.status(SYM)["upstream"] == "connected" and len(delays) == 2)
    assert delays == [1.0, 2.0]
    # after the gap, history since the last official candle is fetched (backfill)
    assert any(c[3] == mk(0).time for c in prov.calls if c[3] is not None)
    msgs = []
    while not sub.queue.empty():
        msgs.append(sub.queue.get_nowait())
    kinds = [m["type"] for m in msgs]
    assert "reconcile" in kinds and kinds.count("status") >= 2
    assert any(m["type"] == "status" and m["upstream"] == "reconnecting" and m["stale"] for m in msgs)
    await hub.close()


async def test_heartbeat_timeout_triggers_reconnect():
    prov = FakeProvider({(SYM, TF): [mk(0), mk(1, complete=False)]},
                        scripts=[[Heartbeat(1), "hang"], [Heartbeat(2), "hang"]])
    hub, delays = make_hub(prov, heartbeat_timeout=0.05)
    await hub.subscribe(SYM, TF)
    await until(lambda: prov.streams_opened >= 2)
    assert delays and prov.streams_closed >= 1
    await hub.close()


async def test_live_ticks_update_then_official_candle_replaces_provisional():
    prov = FakeProvider({(SYM, TF): [mk(0), mk(1, complete=False)]})
    nxt = mk(2, complete=False).time

    def official_now():
        prov.data[(SYM, TF)] = [mk(0), mk(1), mk(2, complete=False)]

    prov.scripts = [[Heartbeat(1), tick(1.5, mk(1).time + 10), official_now, tick(1.6, nxt + 3), "hang"]]
    hub, _ = make_hub(prov, min_reconcile_gap=0.0)
    sub, _ = await hub.subscribe(SYM, TF)
    await until(lambda: hub.status(SYM)["last_price_at"] == nxt + 3)
    await until(lambda: any(m["type"] == "reconcile" for m in list(sub.queue._queue)))
    msgs = list(sub.queue._queue)
    candles = [m["candle"] for m in msgs if m["type"] == "candle"]
    assert candles[0]["close"] == 1.5 and candles[0]["complete"] is False
    assert any(c["provisional"] and c["complete"] for c in candles)      # clock-closed, awaiting confirmation
    rec = next(m for m in msgs if m["type"] == "reconcile")
    by_time = {c["time"]: c for c in rec["candles"]}
    assert by_time[mk(1).time]["complete"] and not by_time[mk(1).time]["provisional"]
    await hub.close()


async def test_market_open_closed_follows_tradeable_flag():
    prov = FakeProvider({(SYM, TF): [mk(0, complete=False)]},
                        scripts=[[Heartbeat(1), tick(1.0, mk(0).time + 1, tradeable=False), "hang"]])
    hub, _ = make_hub(prov)
    await hub.subscribe(SYM, TF)
    await until(lambda: hub.status(SYM)["market_open"] is False)
    await hub.close()


async def test_subscription_management_shares_and_releases_streams():
    prov = FakeProvider({(SYM, "1m"): [mk(0, complete=False)], (SYM, "5m"): [mk(0, step=300, complete=False)]})
    hub, _ = make_hub(prov)
    a, _ = await hub.subscribe(SYM, "1m")
    b, _ = await hub.subscribe(SYM, "5m")
    await until(lambda: prov.streams_opened == 1)
    assert hub.active_streams() == 1
    await hub.unsubscribe(a)
    assert hub.active_streams() == 1
    await hub.unsubscribe(b)
    assert hub.active_streams() == 0
    await until(lambda: prov.streams_closed == 1)


async def test_subscribe_raises_when_provider_down():
    prov = FakeProvider(); prov.fail = True
    hub, _ = make_hub(prov)
    try:
        await hub.subscribe(SYM, TF)
        raise AssertionError("expected failure")
    except ProviderUnavailable:
        assert hub.active_streams() == 0
