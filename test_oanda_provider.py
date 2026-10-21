import httpx
import pytest

from app.config import Settings
from app.providers import oanda
from app.providers.base import INSTRUMENTS, TF_SECONDS, TIMEFRAMES, Heartbeat, InstrumentUnavailable, PriceTick, ProviderUnavailable
from app.providers.factory import UnconfiguredProvider, build_provider
from app.providers.oanda import GRANULARITY, OandaProvider
from tests.helpers import NOW, mk, raw

KEY = "SECRET-KEY-123"
S = Settings(oanda_api_key=KEY, oanda_account_id="101-001-1-001", oanda_env="practice")
ALL = {v["provider"] for v in INSTRUMENTS.values()}


def make(handler, settings=S, sleeps=None):
    async def sleep(d):
        if sleeps is not None:
            sleeps.append(d)
    return OandaProvider(settings, transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: NOW)


def instruments_response(names=ALL):
    return httpx.Response(200, json={"instruments": [{"name": n} for n in names]})


def test_mapping_covers_everything():
    assert set(TIMEFRAMES) == set(GRANULARITY) == set(TF_SECONDS)
    assert {k: v["provider"] for k, v in INSTRUMENTS.items()} == {
        "XAUUSD": "XAU_USD", "USDJPY": "USD_JPY", "NASDAQ": "NAS100_USD", "US30": "US30_USD",
        "EURUSD": "EUR_USD", "GBPUSD": "GBP_USD"}
    assert GRANULARITY == {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4", "1d": "D", "1w": "W"}


async def test_every_instrument_and_timeframe_hits_the_right_endpoint():
    seen = []

    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        seen.append((req.url.path, req.url.params["granularity"], req.url.params["price"]))
        assert req.headers["Authorization"] == f"Bearer {KEY}" and req.url.host == "api-fxpractice.oanda.com"
        return httpx.Response(200, json={"candles": [raw(mk(0))]})

    p = make(handler)
    for sym, meta in INSTRUMENTS.items():
        for tf in TIMEFRAMES:
            await p.candles(sym, tf, 1)
            assert seen[-1] == (f"/v3/instruments/{meta['provider']}/candles", GRANULARITY[tf], "M")


async def test_live_env_uses_live_hosts():
    live = Settings(oanda_api_key=KEY, oanda_account_id="1", oanda_env="live")
    assert live.rest_host == "https://api-fxtrade.oanda.com" and live.stream_host == "https://stream-fxtrade.oanda.com"
    assert S.stream_host == "https://stream-fxpractice.oanda.com"


async def test_backward_paging_returns_ordered_unique(monkeypatch):
    monkeypatch.setattr(oanda, "MAX_COUNT", 3)
    series = [mk(i) for i in range(10)]
    reqs = []

    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        q = req.url.params
        reqs.append(dict(q))
        lst = [c for c in series if "to" not in q or c.time <= float(q["to"])]
        return httpx.Response(200, json={"candles": [raw(c) for c in lst[-int(q["count"]):]]})

    out = await make(handler).candles("EURUSD", "1m", 7)
    assert [c.time for c in out] == [c.time for c in series[-7:]]
    assert len(reqs) >= 3


async def test_forward_paging_since(monkeypatch):
    monkeypatch.setattr(oanda, "MAX_COUNT", 3)
    series = [mk(i) for i in range(10)]

    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        q = req.url.params
        lst = [c for c in series if c.time >= float(q["from"])][: int(q["count"])]
        return httpx.Response(200, json={"candles": [raw(c) for c in lst]})

    out = await make(handler).candles("EURUSD", "1m", 0, since=series[0].time)
    assert [c.time for c in out] == [c.time for c in series]


async def test_rate_limit_retries_with_retry_after():
    n = {"i": 0}
    sleeps = []

    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"candles": [raw(mk(0))]})

    out = await make(handler, sleeps=sleeps).candles("EURUSD", "1m", 1)
    assert len(out) == 1 and sleeps == [7.0]


async def test_network_error_then_success():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            raise httpx.ConnectError("down")
        return instruments_response()

    assert await make(handler).available() == ALL


async def test_credentials_rejected_never_leaks_key():
    p = make(lambda req: httpx.Response(401, json={"errorMessage": f"bad {KEY}"}))
    with pytest.raises(ProviderUnavailable) as e:
        await p.candles("EURUSD", "1m", 5)
    assert KEY not in str(e.value)


async def test_server_errors_exhaust_retries():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(503)

    with pytest.raises(ProviderUnavailable):
        await make(handler).candles("EURUSD", "1m", 5)
    assert n["i"] == 4


async def test_instrument_not_available_for_account():
    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response(ALL - {"XAU_USD"})
        return httpx.Response(200, json={"candles": [raw(mk(0))]})

    p = make(handler)
    with pytest.raises(InstrumentUnavailable):
        await p.candles("XAUUSD", "1m", 1)
    assert len(await p.candles("EURUSD", "1m", 1)) == 1
    with pytest.raises(InstrumentUnavailable):
        await p.candles("FAKE", "1m", 1)


async def test_malformed_response_is_unavailable_not_a_crash():
    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        return httpx.Response(200, json={"nope": []})

    with pytest.raises(ProviderUnavailable):
        await make(handler).candles("EURUSD", "1m", 1)


async def test_price_stream_parses_and_skips_garbage():
    body = "\n".join([
        '{"type":"HEARTBEAT","time":"1700000050.0"}', "garbage",
        '{"type":"PRICE","time":"1700000051.0","tradeable":true,"bids":[{"price":"1.1"}],"asks":[{"price":"1.2"}]}', ""])

    def handler(req):
        if req.url.path.endswith("/instruments"):
            return instruments_response()
        assert req.url.path == "/v3/accounts/101-001-1-001/pricing/stream" and req.url.host == "stream-fxpractice.oanda.com"
        assert req.url.params["instruments"] == "EUR_USD"
        return httpx.Response(200, content=body.encode())

    events = [e async for e in make(handler).price_stream("EURUSD")]
    assert isinstance(events[0], Heartbeat) and isinstance(events[1], PriceTick) and len(events) == 2


async def test_price_stream_auth_failure():
    def handler(req):
        return instruments_response() if req.url.path.endswith("/instruments") else httpx.Response(401)

    with pytest.raises(ProviderUnavailable):
        [e async for e in make(handler).price_stream("EURUSD")]


def test_missing_credentials_and_bad_env():
    p = build_provider(Settings())
    assert isinstance(p, UnconfiguredProvider) and "OANDA_API_KEY" in p.reason
    assert "OANDA_ENV" in build_provider(Settings(oanda_api_key="k", oanda_account_id="a", oanda_env="demo")).reason
    assert KEY not in repr(S)


async def test_unconfigured_provider_raises():
    p = build_provider(Settings())
    with pytest.raises(ProviderUnavailable):
        await p.candles("EURUSD", "1m", 1)
    with pytest.raises(ProviderUnavailable):
        [e async for e in p.price_stream("EURUSD")]
