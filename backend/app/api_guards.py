from collections import deque
from math import ceil
from time import monotonic
from typing import Callable


class SlidingWindowLimiter:
    """Small process-local limiter for expensive local API requests."""

    def __init__(self, limit: int = 12, window_seconds: int = 60, clock: Callable[[], float] = monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._requests: dict[str, deque[float]] = {}

    def retry_after(self, key: str) -> int | None:
        now = self.clock()
        requests = self._requests.setdefault(key, deque())
        while requests and now - requests[0] >= self.window_seconds:
            requests.popleft()
        if len(requests) >= self.limit:
            return max(1, ceil(self.window_seconds - (now - requests[0])))
        requests.append(now)
        if len(self._requests) > 1024:
            self._requests = {addr: events for addr, events in self._requests.items()
                              if events and now - events[-1] < self.window_seconds}
        return None
