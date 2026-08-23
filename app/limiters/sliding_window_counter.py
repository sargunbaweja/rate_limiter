"""In-memory sliding-window-counter rate limiter (approximate sliding window)."""

import time
from threading import Lock


class SlidingWindowCounterLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._counters: dict[str, tuple[int, int, int]] = {}
        self._lock = Lock()

    def _current_window(self, now: float) -> int:
        return int(now) // self.window_seconds

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window = self._current_window(now)

        with self._lock:
            stored_window, current_count, previous_count = self._counters.get(key, (window, 0, 0))

            if stored_window == window:
                pass
            elif stored_window == window - 1:
                previous_count = current_count
                current_count = 0
            else:
                previous_count = 0
                current_count = 0

            elapsed_in_window = now - (window * self.window_seconds)
            weight = max(0.0, 1 - elapsed_in_window / self.window_seconds)
            estimated_count = previous_count * weight + current_count

            if estimated_count >= self.limit:
                self._counters[key] = (window, current_count, previous_count)
                return False

            self._counters[key] = (window, current_count + 1, previous_count)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._counters.pop(key, None)
