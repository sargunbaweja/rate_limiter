"""Redis-backed token-bucket rate limiter (atomic Lua script)."""

import math
import time

from redis.asyncio import Redis

# Runs entirely inside Redis as one atomic step: no other command, from any
# client, can execute in the middle of this script. That's what closes the
# read-modify-write race a plain GET-then-SET would have.
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'timestamp')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HSET', key, 'tokens', tokens, 'timestamp', now)
redis.call('EXPIRE', key, ttl)

return allowed
"""


class RedisTokenBucketLimiter:
    def __init__(self, redis: Redis, capacity: int, refill_rate: float, key_prefix: str = "rl:bucket"):
        self.redis = redis
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.key_prefix = key_prefix
        self._script = redis.register_script(_TOKEN_BUCKET_SCRIPT)
        # long enough for an empty bucket to fully refill, plus a buffer
        self._ttl_seconds = math.ceil(capacity / refill_rate) + 1

    def _key(self, client_key: str) -> str:
        return f"{self.key_prefix}:{client_key}"

    async def is_allowed(self, client_key: str) -> bool:
        now = time.time()
        allowed = await self._script(
            keys=[self._key(client_key)],
            args=[self.capacity, self.refill_rate, now, self._ttl_seconds],
        )
        return bool(allowed)

    async def reset(self, client_key: str) -> None:
        await self.redis.delete(self._key(client_key))
