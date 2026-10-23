from app.api_guards import SlidingWindowLimiter
from app.main import app
from app.config import Settings
from app.runtime import build_runtime, get_runtime, set_runtime
from tests.helpers import FakeProvider
import httpx
import pytest


def test_sliding_window_limiter_allows_after_window_expires():
    now = [10.0]
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.retry_after("127.0.0.1") is None
    assert limiter.retry_after("127.0.0.1") is None
    assert limiter.retry_after("127.0.0.1") == 60
    now[0] = 70.0
    assert limiter.retry_after("127.0.0.1") is None


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/instruments"),
    ("GET", "/api/candles?symbol=XAUUSD&tf=15m"),
    ("GET", "/api/setups/XAUUSD?timeframe=15m"),
    ("POST", "/api/backtests"),
])
async def test_private_single_user_api_rejects_remote_data_requests(method, path):
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path)
    assert response.status_code == 403
    assert response.json()["detail"] == "This API is available only from the local machine."


@pytest.mark.parametrize("vercel_env,expected_status", [("1", 200), (None, 403)])
async def test_vercel_remote_ingress_requires_vercel_function_runtime(monkeypatch, vercel_env, expected_status):
    previous = get_runtime()
    settings = Settings(app_env="production", deployment_mode="vercel", market_data_provider="biquote",
                        allowed_origin="")
    set_runtime(build_runtime(settings, provider=FakeProvider()))
    if vercel_env is None:
        monkeypatch.delenv("VERCEL", raising=False)
    else:
        monkeypatch.setenv("VERCEL", vercel_env)
    try:
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/live")
        assert response.status_code == expected_status
        if expected_status == 403:
            assert "protected Vercel deployment" in response.json()["detail"]
    finally:
        set_runtime(previous)
