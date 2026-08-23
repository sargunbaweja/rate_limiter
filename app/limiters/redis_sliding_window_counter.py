"""Redis-backed sliding-window-counter rate limiter (two counters, weighted estimate)."""

import time

from redis.asyncio import Redis


class RedisSlidingWindowCounterLimiter:
    def __init__(self, redis: Redis, limit: int, window_seconds: int, key_prefix: str = "rl:swc"):
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _key(self, client_key: str, window: int) -> str:
        return f"{self.key_prefix}:{client_key}:{window}"

    async def is_allowed(self, client_key: str) -> bool:
        now = time.time()
        window = int(now) // self.window_seconds
        current_key = self._key(client_key, window)
        previous_key = self._key(client_key, window - 1)

        current = await self.redis.incr(current_key)
        if current == 1:
            # kept alive for 2 windows so it's still readable as "previous"
            # once the next window starts
            await self.redis.expire(current_key, self.window_seconds * 2)

        previous_raw = await self.redis.get(previous_key)
        previous = int(previous_raw) if previous_raw is not None else 0

        elapsed_in_window = now - (window * self.window_seconds)
        weight = max(0.0, 1 - elapsed_in_window / self.window_seconds)
        estimated_count = previous * weight + current

        if estimated_count > self.limit:
            await self.redis.decr(current_key)
            return False

        return True

    async def reset(self, client_key: str) -> None:
        now = time.time()
        window = int(now) // self.window_seconds
        await self.redis.delete(self._key(client_key, window), self._key(client_key, window - 1))
