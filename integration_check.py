"""Runs against a live backend (+ mock OANDA). Verifies REST history, WebSocket live candles, a forced stream drop,
reconnect and backfill over real HTTP/WebSocket connections. Usage: python scripts/integration_check.py"""
import asyncio
import json
import sys

import httpx
import websockets

STEP = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800}
HIER = {'1m': ['5m', '15m'], '5m': ['15m', '1h'], '15m': ['1h', '4h'], '30m': ['1h', '4h'], '1h': ['4h', '1d'], '4h': ['1d', '1w'], '1d': ['1w'], '1w': []}
API, WS = "http://127.0.0.1:8000", "ws://127.0.0.1:8000"


async def main() -> int:
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name, extra)
        ok &= bool(cond)

    async with httpx.AsyncClient() as c:
        h = (await c.get(f"{API}/health")).json()
        check("health reports oanda provider", h["provider"] == "oanda" and h["live"], h)
        inst = (await c.get(f"{API}/api/instruments")).json()["instruments"]
        check("all six instruments available", all(v["available"] for v in inst.values()))
        for tf in ("1m", "15m", "1h", "4h", "1d", "1w"):
            r = (await c.get(f"{API}/api/candles", params={"symbol": "EURUSD", "tf": tf, "count": 50})).json()
            ts = [x["time"] for x in r["candles"]]
            check(f"history {tf}: 50 ordered unique candles, last developing",
                  len(ts) == 50 and ts == sorted(set(ts)) and r["candles"][-1]["complete"] is False
                  and all(x["complete"] for x in r["candles"][:-1]) and r["provider_symbol"] == "EUR_USD")
        big = (await c.get(f"{API}/api/candles", params={"symbol": "EURUSD", "tf": "1m", "count": 5000})).json()
        check("history at the 5000 candle page limit", len(big["candles"]) == 5000)

    async with httpx.AsyncClient(timeout=30) as c:
        import time as _t
        for tf in STEP:
            t0 = _t.perf_counter()
            r = await c.get(f"{API}/api/analysis/EURUSD", params={"timeframe": tf})
            dt = _t.perf_counter() - t0
            a = r.json()
            check(f"analysis {tf}: HTTP 200, status ok/fresh, valid payload ({dt * 1000:.0f} ms incl. MTF)",
                  r.status_code == 200 and a["status"] == "ok" and a["freshness"]["status"] in ("fresh", "market_closed") and dt < 5, a.get("reason"))
            hist = (await c.get(f"{API}/api/candles", params={"symbol": "EURUSD", "tf": tf, "count": 600})).json()["candles"]
            by_t = {x["time"]: x for x in hist}
            step = STEP[tf]
            last_closed = max(x["time"] for x in hist if x["complete"])
            dev_time = max(x["time"] for x in hist if not x["complete"])
            bad = []
            for sw in a["swings"]:
                k = by_t.get(sw["time"])
                if k is None or abs(k["high" if sw["kind"] == "high" else "low"] - sw["price"]) > 1e-9: bad.append(("swing", sw["id"]))
                if sw["confirmed_at"] != sw["time"] + 4 * step or sw["confirmed_at"] > last_closed + step: bad.append(("swing-confirm", sw["id"]))
            for e in a["events"]:
                k = by_t.get(e["break_time"])
                if k is None or not (k["close"] > e["level"] if e["direction"] == "bullish" else k["close"] < e["level"]): bad.append(("event", e["id"]))
                if e["confirmed_at"] != e["break_time"] + step or e["break_time"] >= dev_time: bad.append(("event-confirm", e["id"]))
                if e["broken_swing_time"] not in by_t: bad.append(("event-swing", e["id"]))
            for z in a["zones"]:
                if z["start_time"] not in by_t or not z["lower"] < z["upper"]: bad.append(("zone", z["id"]))
            for t in a["trendlines"]:
                if any(p["time"] not in by_t for p in t["anchors"]) or t["end_time"] not in by_t or t["end_time"] >= dev_time: bad.append(("trendline", t["id"]))
            check(f"analysis {tf}: {len(a['swings'])} swings/{len(a['events'])} events/{len(a['zones'])} zones/{len(a['trendlines'])} lines align with real candles and nothing uses the developing candle", not bad, bad[:3])
            check(f"analysis {tf}: MTF hierarchy {HIER[tf]}", a["mtf"] is not None and [h["timeframe"] for h in a["mtf"]["higher"]] == HIER[tf])
        t0 = _t.perf_counter(); r2 = await c.get(f"{API}/api/analysis/EURUSD", params={"timeframe": "15m"}); warm = _t.perf_counter() - t0
        check(f"warm (cached) analysis is fast ({warm * 1000:.0f} ms)", r2.status_code == 200 and warm < 1.0)
        for sym in ("XAUUSD", "USDJPY", "NASDAQ", "US30", "EURUSD", "GBPUSD"):
            r = await c.get(f"{API}/api/analysis/{sym}", params={"timeframe": "H1"})
            check(f"analysis {sym} H1 alias", r.status_code == 200 and r.json()["status"] == "ok" and r.json()["provider_symbol"])
        check("invalid instrument -> 400", (await c.get(f"{API}/api/analysis/FAKE", params={"timeframe": "1h"})).status_code == 400)
        check("invalid timeframe -> 400", (await c.get(f"{API}/api/analysis/EURUSD", params={"timeframe": "7m"})).status_code == 400)
        s_ = await c.get(f"{API}/health")
        check("no credentials in health", "local-test-key" not in s_.text)

    seen, upstream, reconcile_after_drop = [], [], False
    async with websockets.connect(f"{WS}/ws?symbol=EURUSD&tf=1m") as ws:
        end = asyncio.get_running_loop().time() + 12
        while asyncio.get_running_loop().time() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 6))
            except asyncio.TimeoutError:
                break
            seen.append(m["type"])
            if m["type"] == "status":
                upstream.append(m["upstream"])
            if m["type"] == "reconcile" and "reconnecting" in upstream:
                reconcile_after_drop = True
    check("ws sends backfill first", seen[:1] == ["backfill"], seen[:3])
    check("ws streams live candle updates", seen.count("candle") >= 3, f"{seen.count('candle')} candle msgs")
    check("stream drop was reported as reconnecting", "reconnecting" in upstream, upstream)
    check("connection recovered", upstream and upstream[-1] == "connected", upstream[-3:])
    check("backfill/reconcile delivered after reconnect", reconcile_after_drop)
    async with httpx.AsyncClient() as c:
        check("closing the socket released the upstream stream", (await c.get(f"{API}/health")).json()["active_streams"] == 0)
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
