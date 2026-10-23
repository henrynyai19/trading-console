import asyncio
import ipaddress
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .analysis.analysis_service import AnalysisConfig
from .analysis.models import AnalysisResponse
from .api_guards import SlidingWindowLimiter
from .backtesting import BacktestResponse, run_backtest
from .backtest_store import BacktestStoreUnavailable
from .config import load_settings, validate_settings
from .observability import configure_logging, request_id_context
from .providers.base import INSTRUMENTS, TIMEFRAMES, TF_SECONDS, ProviderUnavailable
from .runtime import get_runtime
from .setups import SetupResponse, TradeSetup, advance_setup, generate_setups
from .stream import candle_dict

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    rt = get_runtime()
    try:
        validate_settings(rt.settings)
        if rt.settings.deployment_mode == "vercel" and os.environ.get("VERCEL") != "1":
            raise ValueError("DEPLOYMENT_MODE=vercel is supported only inside a Vercel Function")
    except ValueError:
        log.exception("startup_configuration_invalid")
        raise
    if rt.settings.supabase_incomplete:
        log.warning("optional database configuration is incomplete; persistence is disabled",
                    extra={"event": "optional_database_configuration_incomplete"})
    yield
    await rt.hub.close()
    if rt.setup_store:
        await rt.setup_store.close()
    if rt.backtest_store:
        await rt.backtest_store.close()
    close_provider = getattr(rt.provider, "aclose", None)
    if close_provider:
        await close_provider()


app = FastAPI(title="Trading Analysis Console API", lifespan=lifespan)
_startup_settings = load_settings()
app.add_middleware(CORSMiddleware, allow_origins=_startup_settings.allowed_origins, allow_methods=["GET", "POST"], allow_headers=["*"])
_backtest_limiter = SlidingWindowLimiter(limit=12, window_seconds=60)
_backtest_slots = asyncio.Semaphore(2)


def _is_local_peer(host: str | None) -> bool:
    if host == "testclient":
        return True  # Starlette's in-process test client has no socket address.
    try:
        return bool(host) and ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _vercel_runtime_enabled() -> bool:
    """Trust remote ingress only inside Vercel, where project-wide auth is enforced."""
    settings = get_runtime().settings
    return settings.deployment_mode == "vercel" and os.environ.get("VERCEL") == "1"


def _websocket_origin_allowed(origin: str | None, headers, settings=None) -> bool:
    settings = settings or get_runtime().settings
    if settings.deployment_mode == "vercel":
        if not origin:
            return False
        parsed = urlsplit(origin)
        forwarded_host = headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
        expected_host = forwarded_host or headers.get("host", "")
        return parsed.scheme == "https" and bool(expected_host) and parsed.netloc.casefold() == expected_host.casefold()
    return not origin or origin.rstrip("/") in settings.allowed_origins


@app.middleware("http")
async def local_api_only(request: Request, call_next):
    host = request.client.host if request.client else None
    if not _is_local_peer(host) and not _vercel_runtime_enabled():
        if get_runtime().settings.deployment_mode == "vercel":
            detail = "This API is available only inside the protected Vercel deployment."
        else:
            detail = "This API is available only from the local machine."
        return JSONResponse({"detail": detail}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = uuid4().hex
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path not in {"/health/live", "/health/ready"}:
            log.info("http_request_complete", extra={
                "event": "http_request_complete", "request_id": request_id, "method": request.method,
                "path": request.url.path, "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        return response
    finally:
        request_id_context.reset(token)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", uuid4().hex)
    log.error("http_request_failed", extra={
        "event": "http_request_failed", "request_id": request_id, "method": request.method,
        "path": request.url.path, "status_code": 500, "error_type": type(exc).__name__,
    })
    return JSONResponse({"detail": "Internal server error.", "request_id": request_id}, status_code=500,
                        headers={"X-Request-ID": request_id})


def _check_backtest_rate(request: Request) -> None:
    host = request.client.host if request.client else "unknown"
    retry_after = _backtest_limiter.retry_after(host)
    if retry_after is not None:
        raise HTTPException(429, "Backtest request limit reached; wait before submitting another run.",
                            headers={"Retry-After": str(retry_after)})


async def _run_backtest_off_thread(*args, **kwargs):
    if _backtest_slots.locked():
        raise HTTPException(429, "Backtest capacity is busy; retry shortly.", headers={"Retry-After": "2"})
    await _backtest_slots.acquire()
    try:
        return await asyncio.to_thread(run_backtest, *args, **kwargs)
    finally:
        _backtest_slots.release()


class BacktestRequest(BaseModel):
    instrument: str
    timeframe: str
    start_time: int = Field(ge=0)
    end_time: int = Field(gt=0)
    strategy: Literal["structure_breaks"] = "structure_breaks"
    risk_reward: Literal[1.5, 2.0, 3.0] = 2.0
    spread_ticks: float = Field(default=0, ge=0, le=1000)
    slippage_ticks: float = Field(default=0, ge=0, le=1000)
    commission_ticks_per_side: float = Field(default=0, ge=0, le=1000)
    in_sample_percent: int = Field(default=60, ge=20, le=90)
    validation_percent: int = Field(default=20, ge=0, le=70)
    left: int = Field(default=3, ge=1, le=10)
    right: int = Field(default=3, ge=1, le=10)
    min_swing_strength: float = Field(default=10, ge=0, le=100)

    @model_validator(mode="after")
    def validate_periods(self):
        if self.in_sample_percent + self.validation_percent >= 100:
            raise ValueError("in_sample_percent and validation_percent must leave a non-empty out-of-sample period")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be later than start_time")
        return self


@app.get("/health")
async def health():
    rt = get_runtime()
    test_mock = bool(rt.settings.oanda_rest_url or rt.settings.oanda_stream_url)
    configured = rt.provider.name in ("oanda", "biquote") and (rt.provider.name != "oanda" or rt.settings.oanda_configured and rt.settings.env_valid)
    mode = "test_mock_endpoint" if test_mock else "configured_provider" if configured else "unconfigured"
    feeds = rt.hub.health_status()
    market_data = "not_observed" if not feeds else "healthy" if all(f["upstream"] == "connected" and not f["stale"] for f in feeds) else "degraded"
    open_feeds = [f for f in feeds if f["market_open"] is True]
    if not feeds:
        freshness = "unknown"
    elif market_data != "healthy":
        freshness = "unavailable"
    elif open_feeds:
        freshness = "fresh" if all(f["last_price_at"] is not None and time.time() - f["last_price_at"] <= 15 for f in open_feeds) else "stale"
    elif all(f["market_open"] is False for f in feeds):
        freshness = "market_closed"
    else:
        freshness = "unknown"
    health_status = "ok" if market_data == "healthy" else "degraded"
    return {"status": health_status, "timestamp": time.time(), "provider": rt.provider.name,
            "environment": rt.settings.oanda_env if rt.provider.name == "oanda" else None,
            "configured": configured and not test_mock, "live": freshness == "fresh" and configured and not test_mock,
            "data_mode": mode, "cache": rt.cache is not None,
            "active_streams": rt.hub.active_streams(), "feeds": feeds,
            "checks": {"process": "ok", "configuration": "configured" if configured and not test_mock else "unconfigured",
                       "market_data": market_data, "data_freshness": freshness,
                       "database": "configuration_incomplete" if rt.settings.supabase_incomplete else
                       "configured_unchecked" if rt.settings.supabase_configured else "not_configured"}}


@app.get("/health/live")
async def liveness():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/health/ready")
async def readiness():
    rt = get_runtime()
    provider_ready = rt.provider.name in ("biquote", "oanda") and (rt.provider.name != "oanda" or rt.settings.oanda_configured and rt.settings.env_valid)
    if not provider_ready:
        raise HTTPException(503, {"status": "not_ready", "reason": "A valid market-data provider is not configured."})
    return {"status": "ready", "timestamp": time.time(), "market_data": "not_observed_until_a_feed_connects"}


def require_live_market_data() -> None:
    """Keep test fixtures from being mistaken for live market data or analysis."""
    rt = get_runtime()
    if rt.provider.name == "oanda" and (rt.settings.oanda_rest_url or rt.settings.oanda_stream_url):
        raise HTTPException(503, {"status": "unavailable", "reason": "Synthetic test data is active. Select a real market data provider to run live market analysis."})
    if rt.provider.name == "none":
        reason = getattr(rt.provider, "reason", "Choose a live provider in backend/.env.")
        raise HTTPException(503, {"status": "unavailable", "reason": reason})


@app.get("/api/instruments")
async def instruments():
    rt = get_runtime()
    try:
        avail = await rt.provider.available()
    except ProviderUnavailable:
        avail = None  # unknown, not "unavailable"
    return {"instruments": {k: {**v, "provider": rt.provider.symbol_code(k),
                                "available": None if avail is None else rt.provider.symbol_code(k) in avail}
                            for k, v in INSTRUMENTS.items()}, "timeframes": TIMEFRAMES}


@app.get("/api/candles")
async def candles(symbol: str, tf: str, count: int = 500):
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    rt = get_runtime()
    require_live_market_data()
    try:
        data, source = await rt.service.history(symbol, tf, max(1, min(count, 5000)))
    except ProviderUnavailable as e:
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})
    return {"symbol": symbol, "timeframe": tf, "provider": rt.provider.name,
            "provider_symbol": rt.provider.symbol_code(symbol), "source": source, "data_mode": "configured_provider",
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
    require_live_market_data()
    cfg = AnalysisConfig(left=left, right=right, min_swing_strength=min_swing_strength, include_invalidated=include_invalidated)
    try:
        return await rt.analysis.analyze(symbol, tf, cfg)
    except ProviderUnavailable as e:
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})


@app.get("/api/setups/{instrument}", response_model=SetupResponse)
async def setups(instrument: str, timeframe: str = "1h", left: int = Query(3, ge=1, le=10), right: int = Query(3, ge=1, le=10),
                 min_swing_strength: float = Query(10.0, ge=0, le=100)):
    symbol = instrument.upper()
    tf = _TF_ALIASES.get(timeframe.lower(), timeframe.lower())
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown instrument or timeframe")
    rt = get_runtime()
    require_live_market_data()
    cfg = AnalysisConfig(left=left, right=right, min_swing_strength=min_swing_strength)
    try:
        analysis_result = await rt.analysis.analyze(symbol, tf, cfg)
        candles = await rt.analysis.closed_history(symbol, tf, cfg)
    except ProviderUnavailable as e:
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})
    result = generate_setups(symbol, tf, candles, analysis_result, rt.provider.symbol_code(symbol))
    if not rt.setup_store or result.status != "ok" or not candles:
        return result

    history = await rt.setup_store.history(symbol, tf, 100)
    if history is None:
        await rt.setup_store.upsert(result.setups)  # best effort; never claim the read/write history is available
        result.history_persistent = False
        return result

    active_states = {"developing", "awaiting_confirmation", "confirmed", "active_reference", "entry_touched",
                     "target_1_reached", "target_2_reached", "active"}
    terminal_ids = {s.id for s in history if s.status in {"target_3_reached", "stopped_out", "invalidated", "expired", "target_touched"}}
    bars = [c for c in candles if c.complete and not c.provisional]
    advanced: list[TradeSetup] = []
    for previous in history:
        if previous.status not in active_states:
            continue
        cursor = previous.last_processed_time
        if cursor is None:
            cursor = previous.detected_at or previous.created_at
        current = previous
        for candle in bars:
            if candle.time >= cursor:
                current = advance_setup(current, candle, candle.time + TF_SECONDS[tf])
                cursor = candle.time + 1
        advanced.append(current)

    generated = {s.id: s for s in result.setups if s.id not in terminal_ids}
    merged: dict[str, TradeSetup] = {s.id: s for s in advanced if s.status not in terminal_ids and s.status in active_states}
    for setup_id, setup in generated.items():
        merged.setdefault(setup_id, setup)  # persisted lifecycle wins over a regenerated candidate
    result.setups = sorted(merged.values(), key=lambda s: (s.quality_score, s.created_at), reverse=True)[:3]
    snapshots = {s.id: s for s in history}
    snapshots.update({s.id: s for s in advanced})
    for setup_id, setup in generated.items():
        snapshots.setdefault(setup_id, setup)
    result.history_persistent = await rt.setup_store.upsert(list(snapshots.values()))
    return result


@app.get("/api/setups/{instrument}/history")
async def setup_history(instrument: str, timeframe: str = "1h", limit: int = Query(50, ge=1, le=200)):
    symbol = instrument.upper()
    tf = _TF_ALIASES.get(timeframe.lower(), timeframe.lower())
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    store = get_runtime().setup_store
    if not store:
        return {"persistent": False, "setups": []}
    history = await store.history(symbol, tf, limit)
    return {"persistent": history is not None, "setups": [s.model_dump(mode="json") for s in history or []]}


@app.get("/api/backtest/{instrument}", response_model=BacktestResponse)
async def backtest(request: Request, instrument: str, timeframe: str = "1h", count: int = Query(1000, ge=61, le=5000),
                   risk_reward: float = Query(2.0), spread_ticks: float = Query(0.0, ge=0, le=1000),
                   slippage_ticks: float = Query(0.0, ge=0, le=1000), left: int = Query(3, ge=1, le=10),
                   right: int = Query(3, ge=1, le=10), min_swing_strength: float = Query(10.0, ge=0, le=100)):
    symbol = instrument.upper()
    tf = _TF_ALIASES.get(timeframe.lower(), timeframe.lower())
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    _check_backtest_rate(request)
    try:
        candles, source = await get_runtime().service.history(symbol, tf, count)
        rt = get_runtime()
        result = await _run_backtest_off_thread(symbol, tf, candles, risk_reward, spread_ticks, slippage_ticks, left, right,
                                                min_swing_strength, provider_symbol=rt.provider.symbol_code(symbol))
        result.data_source = source
        result.data_mode = "test_mock_endpoint" if rt.provider.name == "oanda" and (rt.settings.oanda_rest_url or rt.settings.oanda_stream_url) else "configured_provider"
        return result
    except ProviderUnavailable as e:
        log.warning("backtest_failed", extra={"event": "backtest_failed", "request_id": request.state.request_id,
                                              "symbol": symbol, "timeframe": tf, "error_type": type(e).__name__})
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})
    except ValueError as e:
        log.warning("backtest_failed", extra={"event": "backtest_failed", "request_id": request.state.request_id,
                                              "symbol": symbol, "timeframe": tf, "error_type": type(e).__name__})
        raise HTTPException(422, str(e))


@app.post("/api/backtests", response_model=BacktestResponse)
async def create_backtest(request: Request, payload: BacktestRequest):
    """Bounded synchronous historical run; full ledger and equity series are returned together."""
    _check_backtest_rate(request)
    symbol = payload.instrument.upper()
    tf = _TF_ALIASES.get(payload.timeframe.lower(), payload.timeframe.lower())
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    step = TF_SECONDS[tf]
    if (payload.end_time - payload.start_time) / step > 4900:
        raise HTTPException(422, "Date range exceeds 4,900 candles; choose a shorter range")
    rt = get_runtime()
    try:
        warmup_start = max(0, payload.start_time - 100 * step)
        raw = await rt.service.since(symbol, tf, warmup_start)
        raw = [c for c in raw if c.time <= payload.end_time]
        result = await _run_backtest_off_thread(
            symbol, tf, raw, payload.risk_reward, payload.spread_ticks,
            payload.slippage_ticks, payload.left, payload.right, payload.min_swing_strength,
            rt.provider.symbol_code(symbol), payload.in_sample_percent, payload.validation_percent,
            payload.commission_ticks_per_side, payload.start_time, payload.end_time,
        )
        result.data_source = rt.provider.name
        result.data_mode = "test_mock_endpoint" if rt.provider.name == "oanda" and (rt.settings.oanda_rest_url or rt.settings.oanda_stream_url) else "configured_provider"
        if rt.backtest_store:
            result.backtest_id = uuid4()
            if not await rt.backtest_store.save(result.backtest_id, result):
                result.backtest_id = None
        return result
    except ProviderUnavailable as e:
        log.warning("backtest_failed", extra={"event": "backtest_failed", "request_id": request.state.request_id,
                                              "symbol": symbol, "timeframe": tf, "error_type": type(e).__name__})
        raise HTTPException(503, {"status": "unavailable", "reason": str(e)})
    except ValueError as e:
        log.warning("backtest_failed", extra={"event": "backtest_failed", "request_id": request.state.request_id,
                                              "symbol": symbol, "timeframe": tf, "error_type": type(e).__name__})
        raise HTTPException(422, str(e))


@app.get("/api/backtests/{backtest_id}", response_model=BacktestResponse)
async def get_backtest(backtest_id: UUID):
    store = get_runtime().backtest_store
    if not store:
        raise HTTPException(503, "Backtest persistence is unavailable; configure Supabase and apply its migration.")
    try:
        result = await store.get(backtest_id)
    except BacktestStoreUnavailable as exc:
        raise HTTPException(503, "Supabase backtest history is unavailable.") from exc
    if result is None:
        raise HTTPException(404, "Backtest was not found or its stored result is unavailable.")
    return result


@app.get("/api/backtests/{backtest_id}/trades")
async def get_backtest_trades(backtest_id: UUID):
    result = await get_backtest(backtest_id)
    return {"backtest_id": str(backtest_id), "trades": [trade.model_dump(mode="json") for trade in result.trades]}


@app.get("/api/backtests/{backtest_id}/equity-curve")
async def get_backtest_equity(backtest_id: UUID):
    result = await get_backtest(backtest_id)
    return {"backtest_id": str(backtest_id), "equity_curve": [point.model_dump(mode="json") for point in result.equity_curve],
            "drawdown_curve": [point.model_dump(mode="json") for point in result.drawdown_curve]}


@app.websocket("/ws")
async def ws(websocket: WebSocket, symbol: str, tf: str):
    host = websocket.client.host if websocket.client else None
    if not _is_local_peer(host) and not _vercel_runtime_enabled():
        await websocket.close(code=1008, reason="This API is available only through the protected Vercel deployment.")
        return
    origin = websocket.headers.get("origin")
    if not _websocket_origin_allowed(origin, websocket.headers):
        await websocket.close(code=1008, reason="This application origin is not allowed.")
        return
    if symbol not in INSTRUMENTS or tf not in TIMEFRAMES:
        await websocket.close(code=1008)
        return
    try:
        require_live_market_data()
    except HTTPException as exc:
        await websocket.accept()
        detail = exc.detail if isinstance(exc.detail, dict) else {"reason": str(exc.detail)}
        await websocket.send_json({"type": "error", "status": "unavailable",
                                   "reason": detail.get("reason", "Live market data is unavailable.")})
        await websocket.close(code=1013, reason="Live market data is not configured")
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
