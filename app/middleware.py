"""ASGI middleware that intercepts requests and enforces rate limits."""

import math

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.limiters.redis_fixed_window import RedisFixedWindowLimiter
from app.limiters.redis_sliding_window import RedisSlidingWindowLimiter
from app.limiters.redis_sliding_window_counter import RedisSlidingWindowCounterLimiter
from app.limiters.redis_token_bucket import RedisTokenBucketLimiter
from app.redis_client import get_redis_client

_limiter = None


def _build_limiter():
    settings = get_settings()
    redis = get_redis_client()
    algo = settings.rate_limit_algo

    if algo == "fixed_window":
        return RedisFixedWindowLimiter(redis, settings.rate_limit_limit, settings.rate_limit_window_seconds)
    if algo == "sliding_window":
        return RedisSlidingWindowLimiter(redis, settings.rate_limit_limit, settings.rate_limit_window_seconds)
    if algo == "sliding_window_counter":
        return RedisSlidingWindowCounterLimiter(
            redis, settings.rate_limit_limit, settings.rate_limit_window_seconds
        )
    if algo == "token_bucket":
        return RedisTokenBucketLimiter(redis, settings.rate_limit_capacity, settings.rate_limit_refill_rate)

    raise ValueError(f"Unknown rate limit algorithm: {algo}")


def _get_limiter():
    global _limiter
    if _limiter is None:
        _limiter = _build_limiter()
    return _limiter


def _get_client_key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"

    # Behind a reverse proxy (Railway, Render, nginx, etc.) request.client.host
    # is the proxy's own internal address, not the real caller - it can even
    # change from one request to the next. The real client IP is forwarded in
    # this header instead, as the first (leftmost) address in the list.
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return f"ip:{forwarded_for.split(',')[0].strip()}"

    if request.client:
        return f"ip:{request.client.host}"

    return "ip:unknown"


def _retry_after_seconds(settings) -> int:
    if settings.rate_limit_algo == "token_bucket":
        return max(1, math.ceil(1 / settings.rate_limit_refill_rate))
    return settings.rate_limit_window_seconds


class RateLimiterMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        limiter = _get_limiter()
        client_key = _get_client_key(request)

        allowed = await limiter.is_allowed(client_key)
        if not allowed:
            settings = get_settings()
            retry_after = _retry_after_seconds(settings)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
