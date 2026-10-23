import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.base import ProviderUnavailable
from app.providers.biquote import BiquoteProvider
from app.runtime import build_runtime, set_runtime


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.ConnectError("restricted sandbox; do not expose connection details"), "outbound network access"),
        (httpx.ConnectTimeout("do not expose connection details"), "timed out"),
    ],
)
async def test_biquote_network_errors_are_actionable_and_do_not_leak_details(failure, message):
    def handler(_request):
        raise failure

    provider = BiquoteProvider(client=httpx.AsyncClient(
        base_url="https://biquote.io", transport=httpx.MockTransport(handler),
    ))
    with pytest.raises(ProviderUnavailable) as error:
        await provider.candles("XAUUSD", "15m", 3)
    assert message in str(error.value)
    assert "restricted sandbox" not in str(error.value)
    assert "biquote.io/" not in str(error.value)


async def test_biquote_upstream_503_is_reported_as_provider_unavailable():
    provider = BiquoteProvider(client=httpx.AsyncClient(
        base_url="https://biquote.io",
        transport=httpx.MockTransport(lambda _request: httpx.Response(503)),
    ))
    with pytest.raises(ProviderUnavailable, match=r"temporarily unavailable \(HTTP 503\)"):
        await provider.candles("XAUUSD", "15m", 3)


async def test_biquote_invalid_candle_response_fails_closed():
    provider = BiquoteProvider(client=httpx.AsyncClient(
        base_url="https://biquote.io",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
            "bars": [{"openTime": "bad-time", "open": 1, "high": 2, "low": 1, "close": 2, "isOpen": False}],
        })),
    ))
    with pytest.raises(ProviderUnavailable, match="malformed candle data"):
        await provider.candles("XAUUSD", "15m", 3)


def test_candle_api_preserves_provider_error_and_health_distinction():
    def handler(_request):
        raise httpx.ConnectError("private network detail")

    client = httpx.AsyncClient(base_url="https://biquote.io", transport=httpx.MockTransport(handler))
    provider = BiquoteProvider(client=client)
    set_runtime(build_runtime(Settings(market_data_provider="biquote"), provider=provider))

    with TestClient(app) as test_client:
        candles = test_client.get("/api/candles", params={"symbol": "XAUUSD", "tf": "15m", "count": 3})
        health = test_client.get("/health")
        live = test_client.get("/health/live")

    assert candles.status_code == 503
    assert candles.json()["detail"]["status"] == "unavailable"
    assert "outbound network access" in candles.json()["detail"]["reason"]
    assert "private network detail" not in candles.text
    assert health.status_code == 200 and health.json()["checks"]["process"] == "ok"
    assert health.json()["status"] == "degraded"
    assert health.json()["checks"]["market_data"] == "not_observed"
    assert health.json()["live"] is False
    assert live.status_code == 200 and live.json()["status"] == "ok"


def test_candle_api_rejects_invalid_instrument_and_timeframe():
    with TestClient(app) as test_client:
        unknown_symbol = test_client.get("/api/candles", params={"symbol": "FAKE", "tf": "15m"})
        unknown_timeframe = test_client.get("/api/candles", params={"symbol": "XAUUSD", "tf": "2m"})
    assert unknown_symbol.status_code == 400
    assert unknown_timeframe.status_code == 400
