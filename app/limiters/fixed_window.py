"""In-memory fixed-window rate limiter."""

import time
from threading import Lock


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._counts: dict[str, tuple[int, int]] = {}
        self._lock = Lock()

    def _current_window(self) -> int:
        return int(time.time()) // self.window_seconds

    def is_allowed(self, key: str) -> bool:
        window = self._current_window()

        with self._lock:
            stored_window, count = self._counts.get(key, (window, 0))

            if stored_window != window:
                count = 0

            if count >= self.limit:
                self._counts[key] = (window, count)
                return False

            self._counts[key] = (window, count + 1)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)
