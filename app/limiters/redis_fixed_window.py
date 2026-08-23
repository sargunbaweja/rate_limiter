"""Redis-backed fixed-window rate limiter (INCR + EXPIRE)."""

import time

from redis.asyncio import Redis


class RedisFixedWindowLimiter:
    def __init__(self, redis: Redis, limit: int, window_seconds: int, key_prefix: str = "rl:fixed"):
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _key(self, client_key: str) -> str:
        window = int(time.time()) // self.window_seconds
        return f"{self.key_prefix}:{client_key}:{window}"

    async def is_allowed(self, client_key: str) -> bool:
        key = self._key(client_key)

        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, self.window_seconds)

        return count <= self.limit

    async def reset(self, client_key: str) -> None:
        await self.redis.delete(self._key(client_key))
