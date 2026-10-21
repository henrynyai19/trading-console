import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import app
from app.providers.base import Heartbeat, PriceTick
from app.providers.oanda import OandaProvider
from app.runtime import build_runtime, set_runtime
from tests.helpers import FakeProvider, mk

SYM, TF = "EURUSD", "1m"


def runtime_with(provider):
    rt = build_runtime(Settings(), provider=provider)
    set_runtime(rt)
    return rt


def test_websocket_backfill_status_then_live_candle():
    dev = mk(1, complete=False)
    prov = FakeProvider({(SYM, TF): [mk(0), dev]},
                        scripts=[[Heartbeat(1), PriceTick(dev.time + 5, 2.0, 2.0002, True), "hang"]])
    runtime_with(prov)
    with TestClient(app) as client, client.websocket_connect(f"/ws?symbol={SYM}&tf={TF}") as ws:
        first = ws.receive_json()
        assert first["type"] == "backfill" and [c["time"] for c in first["candles"]] == [mk(0).time, dev.time]
        assert ws.receive_json()["type"] == "status"
        seen = []
        for _ in range(6):
            m = ws.receive_json()
            seen.append(m)
            if m["type"] == "candle":
                break
        c = next(m for m in seen if m["type"] == "candle")["candle"]
        assert c["time"] == dev.time and c["complete"] is False and abs(c["close"] - 2.0001) < 1e-9
    assert prov.streams_closed == 1  # disconnect released the upstream stream


def test_websocket_reports_unavailable_provider_without_fake_data():
    with TestClient(app) as client, client.websocket_connect(f"/ws?symbol={SYM}&tf={TF}") as ws:
        m = ws.receive_json()
        assert m["type"] == "error" and m["status"] == "unavailable" and "OANDA_API_KEY" in m["reason"]


@pytest.mark.parametrize("q", ["symbol=FAKE&tf=1m", "symbol=EURUSD&tf=2m"])
def test_websocket_rejects_invalid_parameters(q):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws?{q}"):
                pass


def test_history_endpoint_shape_and_ordering():
    prov = FakeProvider({(SYM, TF): [mk(0), mk(1), mk(2, complete=False)]})
    runtime_with(prov)
    r = TestClient(app).get("/api/candles", params={"symbol": SYM, "tf": TF, "count": 10}).json()
    times = [c["time"] for c in r["candles"]]
    assert times == sorted(times) and r["provider_symbol"] == "EUR_USD" and r["source"] == "oanda"
    assert r["candles"][-1]["complete"] is False


def test_provider_failure_maps_to_503():
    prov = FakeProvider(); prov.fail = True
    runtime_with(prov)
    r = TestClient(app).get("/api/candles", params={"symbol": SYM, "tf": TF})
    assert r.status_code == 503 and r.json()["detail"]["status"] == "unavailable"


def test_credentials_never_appear_in_any_response():
    key = "SECRET-KEY-123"
    s = Settings(oanda_api_key=key, oanda_account_id="101-001-1-001", oanda_env="practice", supabase_key="SB-SECRET")
    seen_auth = []

    def handler(req):
        seen_auth.append(req.headers.get("Authorization"))
        return httpx.Response(401, json={"errorMessage": f"invalid token {key}"})

    prov = OandaProvider(s, transport=httpx.MockTransport(handler))
    set_runtime(build_runtime(s, provider=prov))
    client = TestClient(app)
    bodies = [client.get("/health").text, client.get("/api/instruments").text,
              client.get("/api/candles", params={"symbol": SYM, "tf": TF}).text]
    with client.websocket_connect(f"/ws?symbol={SYM}&tf={TF}") as ws:
        bodies.append(ws.receive_text())
    assert seen_auth and all(a == f"Bearer {key}" for a in seen_auth)  # the key was used upstream only
    for b in bodies:
        assert key not in b and "SB-SECRET" not in b


def test_health_reports_unconfigured():
    h = TestClient(app).get("/health").json()
    assert h["provider"] == "none" and h["live"] is False and h["cache"] is False
