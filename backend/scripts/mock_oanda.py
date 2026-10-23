"""LOCAL TEST FIXTURE ONLY. A tiny stand-in for OANDA's REST + streaming API so the real adapter can be exercised
over real HTTP. Its prices are synthetic: never point a trading decision at it. Run: uvicorn scripts.mock_oanda:app --port 8101"""
import asyncio
import json
import math
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
STEP = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D": 86400, "W": 604800}
NAMES = ["XAU_USD", "USD_JPY", "NAS100_USD", "US30_USD", "EUR_USD", "GBP_USD"]
state = {"streams": 0, "drop_first_after": 3.0}


def price_at(t: float) -> float:
    return 1.10 + math.sin(t / 300) * 0.01 + math.sin(t / 4000) * 0.02 + math.sin(t / 37000) * 0.03


@app.get("/v3/accounts/{acct}/instruments")
def instruments(acct: str):
    return {"instruments": [{"name": n} for n in NAMES]}


@app.get("/v3/instruments/{inst}/candles")
def candles(inst: str, request: Request):
    q = request.query_params
    step = STEP[q["granularity"]]
    count = min(int(q.get("count", 500)), 5000)
    now = time.time()
    last = int(now // step) * step
    to = float(q["to"]) if "to" in q else None
    frm = float(q["from"]) if "from" in q else None
    if frm is not None:
        times = [t for t in range(int(frm // step) * step, last + 1, step)][:count]
    else:
        times = [last - (count - 1 - i) * step for i in range(count)]
        if to is not None:
            times = [t for t in times if t <= to]
    out = []
    for t in times:
        o, c = price_at(t), price_at(t + step * 0.9)
        out.append({"time": f"{t}.000000000", "volume": 100, "complete": t != last,
                    "mid": {"o": f"{o:.5f}", "h": f"{max(o, c) + 0.001:.5f}", "l": f"{min(o, c) - 0.001:.5f}", "c": f"{c:.5f}"}})
    return {"instrument": inst, "granularity": q["granularity"], "candles": out}


@app.get("/v3/accounts/{acct}/pricing/stream")
async def stream(acct: str, instruments: str, request: Request):
    state["streams"] += 1
    n = state["streams"]

    async def gen():
        start = time.time()
        i = 0
        while True:
            now = time.time()
            if n == 1 and now - start > state["drop_first_after"]:
                return  # simulate a dropped connection
            p = price_at(now)
            yield json.dumps({"type": "PRICE", "time": f"{now:.6f}", "tradeable": True, "instrument": instruments,
                              "bids": [{"price": f"{p - 0.00005:.5f}"}], "asks": [{"price": f"{p + 0.00005:.5f}"}]}) + "\n"
            if i % 4 == 0:
                yield json.dumps({"type": "HEARTBEAT", "time": f"{now:.6f}"}) + "\n"
            i += 1
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="application/octet-stream")


@app.get("/_state")
def _state():
    return JSONResponse(state)
