"""FastAPI app entrypoint: app instance, middleware registration, routes."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.config import get_settings
from app.middleware import RateLimiterMiddleware
from app.redis_client import close_redis_pool, init_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_redis_pool(settings.redis_url)
    yield
    await close_redis_pool()


app = FastAPI(title="rate-limiter", lifespan=lifespan)
app.add_middleware(RateLimiterMiddleware)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/search")
async def search(q: str = ""):
    return {"query": q, "results": []}


@app.get("/debug/settings")
async def debug_settings():
    settings = get_settings()
    return {
        "rate_limit_algo": settings.rate_limit_algo,
        "rate_limit_limit": settings.rate_limit_limit,
        "rate_limit_window_seconds": settings.rate_limit_window_seconds,
        "rate_limit_capacity": settings.rate_limit_capacity,
        "rate_limit_refill_rate": settings.rate_limit_refill_rate,
    }


@app.get("/debug/client")
async def debug_client(request: Request):
    return {
        "client_host": request.client.host if request.client else None,
        "client_port": request.client.port if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
    }
