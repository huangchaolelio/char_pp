"""FastAPI application entry point."""

import logging
import time
import uuid

from fastapi import FastAPI, Request

from src.api.errors import register_exception_handlers
from src.api.routers import knowledge_base, tasks
from src.api.routers._retired import build_retired_router
from src.api.routers.calibration import router as calibration_router
from src.api.routers.classifications import router as classifications_router
from src.api.routers.coaches import router as coaches_router
# Feature-017: videos.py 已物理下线 —— 全部 4 条 /videos/classifications* 端点
#   并入 classifications.py，旧路径由 _retired.py 哨兵路由接管（返回 404+ENDPOINT_RETIRED）
from src.api.routers.teaching_tips import router as teaching_tips_router
from src.api.routers.standards import router as standards_router
# Feature-017: diagnosis.py 已物理下线 —— 同步 POST /diagnosis 端点并入
#   异步 /api/v1/tasks/diagnosis，旧路径由 _retired.py 哨兵路由接管
from src.api.routers.admin import router as admin_router
from src.api.routers.task_channels import router as task_channels_router
from src.api.routers.extraction_jobs import router as extraction_jobs_router
from src.api.routers.video_preprocessing import router as video_preprocessing_router
# Feature-018 — 三阶段业务总览
from src.api.routers.business_workflow import router as business_workflow_router
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

    # ── Feature-017 统一异常处理器 ────────────────────────────────────────
    # 一次注册三个 handler：AppException / RequestValidationError / Exception
    # 实现位于 src/api/errors.py，严格按章程 v1.4.0 原则 IX 要求渲染 ErrorEnvelope
    register_exception_handlers(app)

    # ── Routers ──────────────────────────────────────────────────────────────
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(knowledge_base.router, prefix="/api/v1")
    # Feature-017: videos_router 已删除，4 条 /videos/classifications* 端点下线
    app.include_router(teaching_tips_router, prefix="/api/v1")
    app.include_router(coaches_router, prefix="/api/v1")
    app.include_router(calibration_router, prefix="/api/v1")
    app.include_router(classifications_router, prefix="/api/v1")
    app.include_router(standards_router, prefix="/api/v1")
    # Feature-017: diagnosis_router 已删除，同步 POST /diagnosis 端点下线
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(task_channels_router, prefix="/api/v1")
    app.include_router(extraction_jobs_router, prefix="/api/v1")
    app.include_router(video_preprocessing_router, prefix="/api/v1")
    # Feature-018 — 三阶段业务总览接口（US1）
    app.include_router(business_workflow_router, prefix="/api/v1")

    # ── Feature-017 哨兵路由（已下线接口）────────────────────────────────
    # 注意：build_retired_router() 返回的 router 其 path 本身已含 /api/v1 前缀，
    # 因此挂载时必须使用空 prefix（否则会变成 /api/v1/api/v1/... 双重前缀）。
    # 必须在所有业务 router 注册之后挂载，以确保已下线路径不会拦截尚存的同名路径
    # （FastAPI 按注册顺序匹配）。
    app.include_router(build_retired_router())

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
