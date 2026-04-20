"""FastAPI application entry point."""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.routers import knowledge_base, tasks
from src.api.routers.videos import router as videos_router
# Import celery_app so it registers as the default Celery app for @shared_task
from src.workers.celery_app import celery_app as _celery_app  # noqa: F401

logger = logging.getLogger(__name__)


def _configure_logging(log_level: str) -> None:
    import sys

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
        stream=sys.stdout,
    )


def create_app() -> FastAPI:
    from src.config import get_settings

    settings = get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(
        title="乒乓球AI智能教练系统",
        description="后端视频分析与专业指导建议 API",
        version="0.1.0",
    )

    # ── Request ID middleware ────────────────────────────────────────────────
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response

    # ── Global exception handler ─────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务内部错误，请稍后重试",
                    "details": {},
                }
            },
        )

    # ── Routers ──────────────────────────────────────────────────────────────
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(knowledge_base.router, prefix="/api/v1")
    app.include_router(videos_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
