"""Small JSON log formatter that only emits explicitly approved fields."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
import os
from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

_FIELDS = (
    "event", "request_id", "method", "path", "status_code", "duration_ms", "error_type",
    "symbol", "timeframe", "attempt", "delay_seconds", "candle_count", "provider", "stage",
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if "request_id" not in payload and request_id_context.get():
            payload["request_id"] = request_id_context.get()
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    for handler in root.handlers:
        handler.setFormatter(JsonLogFormatter())
