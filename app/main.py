"""Точка входу FastAPI-застосунку WatchTrack."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response

from app.config import get_settings
from app.database import engine
from app.errors import register_exception_handlers
from app.routers import health, titles, tracking, users

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()  # коректно закриваємо пул з'єднань при зупинці


app = FastAPI(
    title="WatchTrack API",
    version="0.1.0",
    description="Трекер фільмів і серіалів. Базовий прототип (ЛР1, Інженерія високонавантажених систем).",
    lifespan=lifespan,
)
register_exception_handlers(app)


@app.middleware("http")
async def add_process_time(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Час обробки запиту в заголовку X-Process-Time-Ms — база для майбутніх метрик."""
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
    return response


api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(users.router)
api_v1.include_router(titles.router)
api_v1.include_router(tracking.router)

app.include_router(health.router)
app.include_router(api_v1)
