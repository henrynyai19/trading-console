"""Optional Supabase persistence for analytical setup lifecycle snapshots."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .setups import TradeSetup

log = logging.getLogger("setup_store")


class SupabaseSetupStore:
    def __init__(self, url: str, key: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=f"{url}/rest/v1", timeout=5.0, transport=transport,
                                       headers={"apikey": key, "Authorization": f"Bearer {key}"})

    async def _request(self, method: str, path: str, **kwargs):
        try:
            response = await self._http.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("setup history persistence unavailable (%s)", type(exc).__name__,
                        extra={"event": "setup_persistence_unavailable", "error_type": type(exc).__name__})
            return None

    async def upsert(self, setups: list[TradeSetup]) -> bool:
        if not setups:
            return True
        rows = [{"id": s.id, "instrument": s.instrument, "timeframe": s.timeframe, "status": s.status,
                 "created_at": datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
                 "updated_at": datetime.now(timezone.utc).isoformat(), "payload": s.model_dump(mode="json")}
                for s in setups]
        result = await self._request("POST", "/trade_setups", params={"on_conflict": "id"}, json=rows,
                                     headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return result is not None

    async def active(self, instrument: str, timeframe: str) -> list[TradeSetup] | None:
        rows = await self._request("GET", "/trade_setups", params={
            "select": "payload", "instrument": f"eq.{instrument}", "timeframe": f"eq.{timeframe}",
            "status": "in.(developing,awaiting_confirmation,confirmed,active_reference,entry_touched,target_1_reached,target_2_reached,active)",
            "order": "created_at.desc", "limit": "100"})
        if rows is None:
            return None
        try:
            return [TradeSetup.model_validate(row["payload"]) for row in rows]
        except (ValueError, KeyError, TypeError):
            log.warning("stored setup history had an invalid payload; ignoring it")
            return []

    async def history(self, instrument: str, timeframe: str, limit: int) -> list[TradeSetup] | None:
        rows = await self._request("GET", "/trade_setups", params={
            "select": "payload", "instrument": f"eq.{instrument}", "timeframe": f"eq.{timeframe}",
            "order": "created_at.desc", "limit": str(limit)})
        if rows is None:
            return None
        try:
            return [TradeSetup.model_validate(row["payload"]) for row in rows]
        except (ValueError, KeyError, TypeError):
            log.warning("stored setup history had an invalid payload; ignoring it")
            return []

    async def close(self) -> None:
        await self._http.aclose()
