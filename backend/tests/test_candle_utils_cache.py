from dataclasses import replace

import httpx

from app.cache import SupabaseCache
from app.candle_utils import find_gaps, is_fresh, merge_candles, rank
from app.providers.base import Candle
from app.service import MarketDataService
from tests.helpers import BASE, FakeProvider, mk

URL, KEY = "https://x.supabase.co", "SERVICE-SECRET"


def test_merge_rules_prevent_duplicates_and_downgrades():
    store = {}
    official_closed = mk(0)
    assert len(merge_candles(store, [official_closed, official_closed])) == 1  # duplicate ignored
    developing = Candle(official_closed.time, 1, 2, 0.5, 1.5, 1, False)
    provisional = Candle(official_closed.time, 9, 9, 9, 9, 0, True, True)
    assert merge_candles(store, [developing, provisional]) == []  # cannot downgrade a closed official candle
    assert store[official_closed.time] == official_closed
    store2 = {}
    merge_candles(store2, [provisional])
    merge_candles(store2, [developing])
    assert rank(store2[provisional.time]) == 1  # official developing replaces provisional


def test_completed_provider_correction_is_applied_and_logged(caplog):
    original = mk(0)
    revised = replace(original, close=original.close + 0.01)
    store = {original.time: original}
    with caplog.at_level("WARNING", logger="candles"):
        assert merge_candles(store, [revised]) == [revised]
    assert store[original.time] == revised
    assert "provider corrected a completed candle" in caplog.text


def test_find_gaps_ignores_weekend_but_flags_missing():
    times = [mk(i).time for i in range(10) if i != 4]
    assert find_gaps(times, "1m") == [(mk(4).time, mk(4).time)]
    # Friday 20:59 UTC -> Sunday 22:00 UTC is the normal weekend close for 1m data
    fri, sun = 1700254740, 1700431200  # Fri 20:59 UTC -> Sun 22:00 UTC
    assert find_gaps([fri - 60, fri, sun, sun + 60], "1m") == []


def test_is_fresh():
    assert is_fresh(1000, "1m", 1000 + 60 * 499)
    assert not is_fresh(1000, "1m", 1000 + 60 * 501)


class FakeSupabase:
    def __init__(self):
        self.rows = {}
        self.posts = []
        self.down = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["apikey"] == KEY
        if self.down:
            return httpx.Response(503)
        q = req.url.params
        if req.method == "POST":
            assert q["on_conflict"] == "instrument,timeframe,ts" and "merge-duplicates" in req.headers["Prefer"]
            import json
            body = json.loads(req.content)
            self.posts.append(body)
            for r in body:
                self.rows[(r["instrument"], r["timeframe"], r["ts"])] = r
            return httpx.Response(201)
        inst, tf = q["instrument"][3:], q["timeframe"][3:]
        rows = sorted((r for k, r in self.rows.items() if k[:2] == (inst, tf)), key=lambda r: r["ts"], reverse=True)
        return httpx.Response(200, json=rows[: int(q["limit"])])

    def drop(self, c: Candle):
        for k in [k for k in self.rows if k[2].startswith(__import__("datetime").datetime.fromtimestamp(c.time, __import__("datetime").timezone.utc).isoformat())]:
            del self.rows[k]


def make_cache(fake, clock=lambda: 0.0):
    return SupabaseCache(URL, KEY, transport=httpx.MockTransport(fake), clock=clock)


async def test_cache_roundtrip_filters_and_orders():
    fake = FakeSupabase()
    cache = make_cache(fake)
    cs = [mk(2), mk(0), mk(1), mk(3, complete=False), Candle(mk(4).time, 1, 1, 1, 1, 0, True, True)]
    assert await cache.upsert("EURUSD", "1m", cs) is True
    assert len(fake.posts[0]) == 3  # incomplete and provisional candles are never stored
    got = await cache.get("EURUSD", "1m", 10)
    assert [c.time for c in got] == [mk(0).time, mk(1).time, mk(2).time] and all(c.complete for c in got)
    await cache.upsert("EURUSD", "1m", [mk(1)])  # same key again: upsert, no duplicates
    assert len(fake.rows) == 3


async def test_cache_failure_degrades_and_recovers():
    fake, t = FakeSupabase(), {"now": 0.0}
    cache = make_cache(fake, clock=lambda: t["now"])
    fake.down = True
    assert await cache.get("EURUSD", "1m", 5) is None
    fake.down = False
    assert await cache.get("EURUSD", "1m", 5) is None  # still cooling down
    assert await cache.upsert("EURUSD", "1m", [mk(0)]) is False
    t["now"] = 61
    assert await cache.upsert("EURUSD", "1m", [mk(0)]) is True


async def test_service_works_without_cache():
    prov = FakeProvider({("EURUSD", "1m"): [mk(i) for i in range(5)]})
    data, source = await MarketDataService(prov, None).history("EURUSD", "1m", 3)
    assert source == "oanda" and [c.time for c in data] == [mk(i).time for i in (2, 3, 4)]


async def test_service_survives_supabase_outage():
    fake = FakeSupabase(); fake.down = True
    prov = FakeProvider({("EURUSD", "1m"): [mk(i) for i in range(5)]})
    data, source = await MarketDataService(prov, make_cache(fake)).history("EURUSD", "1m", 3)
    assert source == "oanda" and len(data) == 3


async def test_service_uses_cache_then_fetches_only_the_tail():
    fake = FakeSupabase()
    series = [mk(i) for i in range(60)] + [mk(60, complete=False)]
    prov = FakeProvider({("EURUSD", "1m"): series})
    svc = MarketDataService(prov, make_cache(fake), clock=lambda: BASE + 61 * 60)
    first, s1 = await svc.history("EURUSD", "1m", 50)  # cold cache: full fetch
    assert s1 == "oanda"
    prov.calls.clear()
    second, s2 = await svc.history("EURUSD", "1m", 50)
    assert s2 == "cache+oanda" and prov.calls == [("EURUSD", "1m", 0, mk(59).time)]
    times = [c.time for c in second]
    assert times == sorted(set(times)) and len(second) == 50 and second[-1].complete is False


async def test_service_detects_and_backfills_missing_candles():
    fake = FakeSupabase()
    series = [mk(i) for i in range(120)] + [mk(120, complete=False)]
    prov = FakeProvider({("EURUSD", "1m"): series})
    svc = MarketDataService(prov, make_cache(fake), clock=lambda: BASE + 121 * 60)
    await svc.history("EURUSD", "1m", 120)  # warm the cache with 119 complete rows
    fake.drop(mk(90))
    prov.calls.clear()
    out, _ = await svc.history("EURUSD", "1m", 100)
    assert mk(90).time in {c.time for c in out}
    assert ("EURUSD", "1m", 0, mk(90).time - 60) in prov.calls
    prov.calls.clear()
    await svc.history("EURUSD", "1m", 100)  # same gap again must not be refetched endlessly
    assert all(c[3] != mk(90).time - 60 for c in prov.calls)
