"""Redis connection setup and lifecycle management."""

import os

from redis.asyncio import ConnectionPool, Redis

_pool: ConnectionPool | None = None
_client: Redis | None = None


def get_redis_client() -> Redis:
    if _client is None:
        raise RuntimeError("Redis client not initialized — call init_redis_pool() first")
    return _client


async def init_redis_pool(redis_url: str | None = None) -> Redis:
    global _pool, _client

    redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _pool = ConnectionPool.from_url(redis_url, decode_responses=True)
    _client = Redis(connection_pool=_pool)

    await _client.ping()

    return _client


async def close_redis_pool() -> None:
    global _pool, _client

    if _client is not None:
        await _client.aclose()
    if _pool is not None:
        await _pool.disconnect()

    _client = None
    _pool = None
