"""In-memory token-bucket rate limiter."""

import time
from threading import Lock


class TokenBucketLimiter:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens added per second
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()

        with self._lock:
            tokens, last_refill = self._buckets.get(key, (self.capacity, now))

            elapsed = now - last_refill
            tokens = min(self.capacity, tokens + elapsed * self.refill_rate)

            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False

            self._buckets[key] = (tokens - 1, now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)
