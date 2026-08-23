"""In-memory sliding-window-log rate limiter."""

import time
from collections import deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._requests.setdefault(key, deque())

            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.limit:
                return False

            timestamps.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._requests.pop(key, None)
