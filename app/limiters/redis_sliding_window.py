"""Redis-backed sliding-window-log rate limiter (sorted set)."""

import time
import uuid

from redis.asyncio import Redis


class RedisSlidingWindowLimiter:
    def __init__(self, redis: Redis, limit: int, window_seconds: int, key_prefix: str = "rl:sliding"):
        self.redis = redis
        self.limit = limit
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _key(self, client_key: str) -> str:
        return f"{self.key_prefix}:{client_key}"

    async def is_allowed(self, client_key: str) -> bool:
        key = self._key(client_key)
        now = time.time()
        cutoff = now - self.window_seconds
        member = f"{now}:{uuid.uuid4().hex}"

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, self.window_seconds)
        _, _, count, _ = await pipe.execute()

        if count > self.limit:
            await self.redis.zrem(key, member)
            return False

        return True

    async def reset(self, client_key: str) -> None:
        await self.redis.delete(self._key(client_key))
