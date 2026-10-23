from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_candles_unavailable_without_provider():
    r = client.get("/api/candles", params={"symbol": "XAUUSD", "tf": "15m"})
    assert r.status_code == 503
    assert r.json()["detail"]["status"] == "unavailable"


def test_rejects_unknown_symbol():
    assert client.get("/api/candles", params={"symbol": "FAKE", "tf": "15m"}).status_code == 400


def test_setups_and_backtests_do_not_substitute_mock_prices_without_provider():
    setup = client.get("/api/setups/XAUUSD", params={"timeframe": "15m"})
    backtest = client.get("/api/backtest/XAUUSD", params={"timeframe": "15m"})
    assert setup.status_code == 503
    assert backtest.status_code == 503
    assert "unavailable" in str(setup.json()).lower()
    assert "unavailable" in str(backtest.json()).lower()


def test_setup_history_is_explicitly_nonpersistent_without_supabase():
    result = client.get("/api/setups/XAUUSD/history", params={"timeframe": "15m"})
    assert result.status_code == 200
    assert result.json() == {"persistent": False, "setups": []}
