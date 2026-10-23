"""Optional Supabase persistence for reproducible historical backtest reports."""
from __future__ import annotations

import logging
from uuid import UUID

import httpx

from .backtesting import BacktestResponse

log = logging.getLogger("backtest_store")


class BacktestStoreUnavailable(Exception):
    pass


class SupabaseBacktestStore:
    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=f"{url}/rest/v1", timeout=8.0, transport=transport,
                                       headers={"apikey": key, "Authorization": f"Bearer {key}"})

    async def _request(self, method: str, path: str, **kwargs):
        try:
            response = await self._http.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("backtest persistence unavailable (%s)", type(exc).__name__,
                        extra={"event": "backtest_persistence_unavailable", "error_type": type(exc).__name__})
            return None

    async def save(self, backtest_id: UUID, result: BacktestResponse) -> bool:
        payload = result.model_dump(mode="json")
        payload["result_persisted"] = True
        result.result_persisted = True
        rows = [{"id": str(backtest_id), "instrument": result.instrument, "timeframe": result.timeframe,
                 "strategy_version": result.strategy_version, "data_source": result.data_source,
                 "start_time": result.requested_start, "end_time": result.requested_end, "payload": payload}]
        stored = await self._request("POST", "/backtest_runs", json=rows,
                                     headers={"Prefer": "return=minimal"})
        if stored is None:
            result.result_persisted = False
            return False
        return True

    async def get(self, backtest_id: UUID) -> BacktestResponse | None:
        rows = await self._request("GET", "/backtest_runs", params={
            "select": "payload", "id": f"eq.{backtest_id}", "limit": "1"})
        if rows is None:
            raise BacktestStoreUnavailable("Supabase backtest storage could not be reached")
        if not rows:
            return None
        try:
            return BacktestResponse.model_validate(rows[0]["payload"])
        except (ValueError, KeyError, TypeError):
            log.warning("stored backtest record had an invalid payload; ignoring it")
            return None

    async def close(self) -> None:
        await self._http.aclose()
