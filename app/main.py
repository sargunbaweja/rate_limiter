"""FastAPI app entrypoint: app instance, middleware registration, routes."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
