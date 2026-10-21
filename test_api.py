from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_candles_unavailable_without_provider():
    r = client.get("/api/candles", params={"symbol": "XAUUSD", "tf": "15m"})
    assert r.status_code == 503
    assert r.json()["detail"]["status"] == "unavailable"


def test_rejects_unknown_symbol():
    assert client.get("/api/candles", params={"symbol": "FAKE", "tf": "15m"}).status_code == 400
