import json

import pytest
from fastapi.testclient import TestClient

from app.analysis.analysis_service import AnalysisConfig, AnalysisService
from app.analysis.models import AnalysisResponse
from app.config import Settings
from app.main import app
from app.providers.base import INSTRUMENTS, TF_SECONDS, Candle
from app.runtime import build_runtime, set_runtime
from app.service import MarketDataService
from tests.analysis_helpers import BULL, NOW, developing, zigzag
from tests.helpers import FakeProvider

SYM = "XAUUSD"
LONG = BULL + [144, 135, 148, 139, 152, 143, 156, 147, 160, 151, 164, 155, 168]


def data_for(tfs=("15m", "1h", "4h", "1d"), legs=LONG, with_dev=True):
    d = {}
    for tf in tfs:
        c = zigzag(legs, bars=10, tf=tf, end=NOW - 100)
        d[(SYM, tf)] = c + ([developing(c[-1], tf)] if with_dev else [])
    return d


def make(data, now=NOW):
    prov = FakeProvider(data)
    clock = {"t": float(now)}
    svc = AnalysisService(MarketDataService(prov), clock=lambda: clock["t"])
    return svc, prov, clock


async def test_full_response_with_mtf_and_commentary_is_valid_json():
    svc, prov, _ = make(data_for())
    r = await svc.analyze(SYM, "15m")
    AnalysisResponse.model_validate(r.model_dump())                       # validators accept their own output
    json.dumps(r.model_dump(), allow_nan=False)
    assert r.status == "ok" and r.provider_symbol == "XAU_USD" and r.freshness.status == "fresh"
    assert r.trend.classification in ("Bullish", "Strong Bullish") and r.swings and r.events and r.zones
    assert [h.timeframe for h in r.mtf.higher] == ["1h", "4h"] and r.mtf.bias in ("Bullish", "Leaning Bullish")
    assert r.commentary and "not a probability" in r.commentary[0] and "Higher timeframes" in " ".join(r.commentary)
    assert all(s.confirmed_at > s.time for s in r.swings) and all(e.confirmed_at > e.break_time for e in r.events)
    assert r.swings == sorted(r.swings, key=lambda s: s.time)


async def test_commentary_never_invents_missing_facts():
    d = data_for(("1h",), legs=[101, 102, 103], with_dev=True)
    d[(SYM, "1h")] = zigzag([101, 102, 103], bars=25, tf="1h", end=NOW - 100)
    r = await make(d)[0].analyze(SYM, "1h")
    assert r.trend is None and r.events == [] and not any("structural event" in x for x in r.commentary)


async def test_higher_timeframe_failure_degrades_only_the_mtf_view():
    d = data_for(("15m", "1h"))                                           # 4h has no data at all
    svc, prov, _ = make(d)
    r = await svc.analyze(SYM, "15m")
    assert r.status == "ok"
    h4 = next(h for h in r.mtf.higher if h.timeframe == "4h")
    assert h4.direction == "unavailable" and r.mtf.alignment == "partial"


async def test_primary_provider_failure_propagates():
    from app.providers.base import ProviderUnavailable
    svc, prov, _ = make(data_for())
    prov.fail = True
    with pytest.raises(ProviderUnavailable):
        await svc.analyze(SYM, "15m")


async def test_insufficient_history_is_reported_not_analysed():
    d = {(SYM, "15m"): zigzag(BULL, bars=2, end=NOW - 100)}
    r = await make(d)[0].analyze(SYM, "15m")
    assert r.status == "insufficient_data" and r.swings == [] and r.zones == [] and r.mtf is None and "closed candles" in r.reason


async def test_stale_data_withholds_all_confirmed_analysis():
    svc, prov, clock = make(data_for())
    clock["t"] = NOW + 86400
    r = await svc.analyze(SYM, "15m")
    assert r.status == "stale" and r.freshness.status == "stale"
    assert not (r.swings or r.zones or r.events or r.trendlines or r.trend or r.mtf) and "withheld" in r.reason


async def test_weekend_is_market_closed_not_stale():
    fri_close = 1700254800 - 15 * 60 + 60  # just before the Friday 21:00 UTC close
    c = zigzag(LONG, bars=10, tf="15m", end=fri_close)
    svc, _, clock = make({(SYM, "15m"): c})
    clock["t"] = 1700254800 + 20 * 3600     # Saturday afternoon
    r = await svc.analyze(SYM, "15m")
    assert r.status == "ok" and r.freshness.status == "market_closed"
    clock["t"] = 1700431200 + 300            # Sunday reopen +5 min: still fresh once the closure is removed
    assert (await svc.analyze(SYM, "15m")).status == "ok"


async def test_analysis_is_cached_and_invalidated_by_a_new_completed_candle():
    d = data_for(("15m",))
    svc, prov, clock = make(d)
    await svc.analyze(SYM, "15m")
    n_calls, n_comp = len(prov.calls), svc.computations
    await svc.analyze(SYM, "15m")                                         # within TTL: no provider call, no recompute
    assert len(prov.calls) == n_calls and svc.computations == n_comp
    clock["t"] += 10                                                      # TTL passed: data is refetched
    await svc.analyze(SYM, "15m")
    assert len(prov.calls) > n_calls and svc.computations == n_comp       # ...but the unchanged candles reuse the result
    closed_now = d[(SYM, "15m")][:-1]                                     # the developing candle completes, a new one opens
    done = Candle(closed_now[-1].time + TF_SECONDS["15m"], 1, 2, 0.5, 1.5, 1, True)
    clock["t"] += 10
    d[(SYM, "15m")] = closed_now + [done, developing(done)]
    await svc.analyze(SYM, "15m")
    assert svc.computations == n_comp + 1


async def test_instrument_and_timeframe_switching_keep_results_separate():
    d = data_for(("15m", "1h", "4h", "1d"))
    svc, _, _ = make(d)
    a = await svc.analyze(SYM, "15m")
    b = await svc.analyze(SYM, "1h")
    assert (a.timeframe, b.timeframe) == ("15m", "1h")
    assert a.swings[0].timeframe == "15m" and b.swings[0].timeframe == "1h" and {z.timeframe for z in b.zones} <= {"1h"}


def client_with(data, now=NOW):
    prov = FakeProvider(data)
    rt = build_runtime(Settings(), provider=prov)
    rt.analysis._clock = lambda: float(now)
    set_runtime(rt)
    return TestClient(app), prov


def test_endpoint_shape_aliases_and_validation_errors():
    client, _ = client_with(data_for())
    r = client.get(f"/api/analysis/{SYM}", params={"timeframe": "15m"})
    assert r.status_code == 200
    body = r.json()
    for key in ("instrument", "timeframe", "analysis_time", "freshness", "trend", "swings", "zones", "events", "trendlines", "mtf", "commentary", "status"):
        assert key in body
    assert body["trend"]["score"] == body["trend"]["score"] and body["status"] == "ok"
    assert client.get(f"/api/analysis/{SYM.lower()}", params={"timeframe": "M15"}).status_code == 200   # alias + case
    assert client.get("/api/analysis/FAKE", params={"timeframe": "15m"}).status_code == 400
    assert client.get(f"/api/analysis/{SYM}", params={"timeframe": "2m"}).status_code == 400
    assert client.get(f"/api/analysis/{SYM}", params={"timeframe": "15m", "left": 0}).status_code == 422


def test_endpoint_maps_provider_failure_to_503_and_unconfigured_backend():
    client, prov = client_with(data_for())
    prov.fail = True
    r = client.get(f"/api/analysis/{SYM}", params={"timeframe": "15m"})
    assert r.status_code == 503 and r.json()["detail"]["status"] == "unavailable"
    set_runtime(build_runtime(Settings()))
    assert TestClient(app).get(f"/api/analysis/{SYM}", params={"timeframe": "1h"}).status_code == 503


def test_endpoint_works_for_every_instrument_and_timeframe():
    for sym in INSTRUMENTS:
        scale = {"XAUUSD": 20.0, "USDJPY": 1.5, "NASDAQ": 150.0, "US30": 350.0, "EURUSD": 0.011, "GBPUSD": 0.013}[sym]
        d = {}
        for tf in TF_SECONDS:
            c = zigzag(LONG, bars=10, tf=tf, end=NOW - 100, scale=scale)
            d[(sym, tf)] = c + [developing(c[-1], tf)]
        client, _ = client_with(d)
        for tf in ("15m", "1h"):
            r = client.get(f"/api/analysis/{sym}", params={"timeframe": tf})
            assert r.status_code == 200 and r.json()["status"] == "ok", (sym, tf)
            assert r.json()["trend"]["classification"] in ("Bullish", "Strong Bullish")
