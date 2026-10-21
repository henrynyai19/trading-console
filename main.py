import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .analysis.analysis_service import AnalysisConfig
from .analysis.models import AnalysisResponse
from .config import load_settings
from .providers.base import INSTRUMENTS, TIMEFRAMES, ProviderUnavailable
from .runtime import get_runtime
from .stream import candle_dict

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await get_runtime().hub.close()


app = FastAPI(title="Trading Analysis Console API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[load_settings().allowed_origin], allow_methods=["GET"], allow_headers=["*"])


@app.get("/health")
async def health():
    rt = get_runtime()
    return {"status": "ok", "provider": rt.provider.name, "live": rt.provider.name != "none",
            "cache": rt.cache is not None, "active_streams": rt.hub.active_streams()}


@app.get("/api/instruments")
async def instruments():
    rt = get_runtime()
    try:
        avail = await rt.provider.available()
    except ProviderUnavailable:
        avail = None  # unknown, not "unavailable"
    return {"instruments": {k: {**v, "available": None if avail is None else v["provider"] in avail}
                            for k, v in INSTRUMENTS.items()}, "timeframes": TIMEFRAMES}


@app.get("/api/candles")
async def candles(symbol: str, tf: str, count: int = 500):
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    rt = get_runtime()
    try:
        data, source = await rt.service.history(symbol, tf, max(1, min(count, 5000)))
    except ProviderUnavailable as e:
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})
    return {"symbol": symbol, "timeframe": tf, "provider": rt.provider.name,
            "provider_symbol": INSTRUMENTS[symbol]["provider"], "source": source,
            "candles": [candle_dict(c) for c in data]}


_TF_ALIASES = {"m1": "1m", "m5": "5m", "m15": "15m", "m30": "30m", "h1": "1h", "h4": "4h", "d": "1d", "d1": "1d", "w": "1w", "w1": "1w"}


@app.get("/api/analysis/{instrument}", response_model=AnalysisResponse)
async def analysis(instrument: str, timeframe: str = "1h", left: int = Query(3, ge=1, le=10), right: int = Query(3, ge=1, le=10),
                   min_swing_strength: float = Query(10.0, ge=0, le=100), include_invalidated: bool = False):
    symbol = instrument.upper()
    tf = _TF_ALIASES.get(timeframe.lower(), timeframe.lower())
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown instrument or timeframe")
    rt = get_runtime()
    cfg = AnalysisConfig(left=left, right=right, min_swing_strength=min_swing_strength, include_invalidated=include_invalidated)
    try:
        return await rt.analysis.analyze(symbol, tf, cfg)
    except ProviderUnavailable as e:
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})


@app.websocket("/ws")
async def ws(websocket: WebSocket, symbol: str, tf: str):
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    hub = get_runtime().hub
    try:
        sub, tail = await hub.subscribe(symbol, tf)
    except ProviderUnavailable as e:
        await websocket.send_json({"type": "error", "status": "unavailable", "reason": str(e)})
        await websocket.close(code=1011)
        return
    recv = asyncio.create_task(websocket.receive_text())
    get = None
    try:
        await websocket.send_json({"type": "backfill", "symbol": symbol, "timeframe": tf,
                                   "candles": [candle_dict(c) for c in tail]})
        await websocket.send_json(hub.status(symbol))
        while True:
            get = asyncio.create_task(sub.queue.get())
            done, _ = await asyncio.wait({get, recv}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:
                recv.result()  # raises WebSocketDisconnect when the client leaves
                recv = asyncio.create_task(websocket.receive_text())
            if get in done:
                await websocket.send_json(get.result())
            else:
                get.cancel()
                if not done:  # idle: send a status heartbeat so the client can detect stale connections
                    await websocket.send_json(hub.status(symbol))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in (recv, get):
            if t:
                t.cancel()
        await hub.unsubscribe(sub)
