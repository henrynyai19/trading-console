import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.backtest_store import SupabaseBacktestStore
from app.backtesting import run_backtest
from app.config import Settings
from app.main import app as api_app
from app.runtime import build_runtime, set_runtime
from tests.analysis_helpers import zigzag
from tests.helpers import FakeProvider


def test_post_backtest_returns_dates_periods_ledger_and_data_quality():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    provider = FakeProvider({("XAUUSD", "15m"): candles})
    settings = Settings(market_data_provider="oanda", oanda_api_key="test", oanda_account_id="test",
                        oanda_rest_url="http://mock", oanda_stream_url="http://mock")
    set_runtime(build_runtime(settings, provider=provider))
    body = {"instrument": "XAUUSD", "timeframe": "15m", "start_time": candles[0].time,
            "end_time": candles[-1].time, "spread_ticks": 1, "slippage_ticks": 0.5,
            "commission_ticks_per_side": 0.25, "in_sample_percent": 60, "validation_percent": 20}
    with TestClient(api_app) as client:
        response = client.post("/api/backtests", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["data_mode"] == "test_mock_endpoint"
    assert result["requested_start"] == candles[0].time
    assert result["requested_end"] == candles[-1].time
    assert [p["name"] for p in result["periods"]] == ["in_sample", "validation", "out_of_sample"]
    assert result["data_quality"]["timestamps_utc"] is True
    assert "trades" in result and "equity_curve" in result and result["result_persisted"] is False


def test_post_backtest_rejects_invalid_config_and_oversized_date_range():
    with TestClient(api_app) as client:
        invalid_periods = client.post("/api/backtests", json={"instrument": "XAUUSD", "timeframe": "15m",
            "start_time": 100, "end_time": 200, "in_sample_percent": 80, "validation_percent": 20})
        invalid_tf = client.post("/api/backtests", json={"instrument": "XAUUSD", "timeframe": "bad",
            "start_time": 100, "end_time": 200})
        huge = client.post("/api/backtests", json={"instrument": "XAUUSD", "timeframe": "1m",
            "start_time": 100, "end_time": 100 + 5001 * 60})
    assert invalid_periods.status_code == 422
    assert invalid_tf.status_code == 400
    assert huge.status_code == 422


async def test_backtest_store_saves_reproducible_payload_and_reloads_it():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    result = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0)
    captured: dict = {}

    async def handler(request: httpx.Request):
        if request.method == "POST":
            captured["row"] = json.loads(await request.aread())[0]
            return httpx.Response(201)
        return httpx.Response(200, json=[{"payload": captured["row"]["payload"]}])

    store = SupabaseBacktestStore("https://db.example", "server-secret", transport=httpx.MockTransport(handler))
    backtest_id = uuid4()
    result.backtest_id = backtest_id
    assert await store.save(backtest_id, result)
    restored = await store.get(backtest_id)
    await store.close()
    assert result.result_persisted is True
    assert restored is not None and restored.backtest_id == backtest_id
    assert restored.candle_fingerprint == result.candle_fingerprint
    assert restored.trades == result.trades
    assert "server-secret" not in json.dumps(captured["row"])


def test_backtest_retrieval_routes_return_saved_report_trades_and_equity():
    candles = zigzag([110, 102, 112, 106, 116, 108, 120, 111, 124, 115, 128, 110, 118, 100, 108, 94, 104, 88],
                     bars=8, tf="15m", end=1_800_000_000)
    report = run_backtest("XAUUSD", "15m", candles, min_swing_strength=0)
    backtest_id = uuid4()
    report.backtest_id = backtest_id
    report.result_persisted = True

    class StoreStub:
        async def get(self, requested_id):
            return report if requested_id == backtest_id else None

        async def close(self):
            return None

    runtime = build_runtime(Settings(), provider=FakeProvider())
    runtime.backtest_store = StoreStub()
    set_runtime(runtime)
    with TestClient(api_app) as client:
        full = client.get(f"/api/backtests/{backtest_id}")
        trades = client.get(f"/api/backtests/{backtest_id}/trades")
        equity = client.get(f"/api/backtests/{backtest_id}/equity-curve")
    assert full.status_code == 200 and full.json()["result_persisted"] is True
    assert trades.status_code == 200 and len(trades.json()["trades"]) == len(report.trades)
    assert equity.status_code == 200 and len(equity.json()["equity_curve"]) == len(report.equity_curve)
