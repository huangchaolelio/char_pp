"""FastAPI application entry point."""

import logging
import time
import uuid

from fastapi import FastAPI, Request

from src.api.errors import register_exception_handlers
from src.api.routers import knowledge_base, tasks
from src.api.routers.calibration import router as calibration_router
from src.api.routers.classifications import router as classifications_router
from src.api.routers.athlete_classifications import router as athlete_classifications_router
from src.api.routers.athlete_tasks import router as athlete_tasks_router
from src.api.routers.diagnosis_reports import router as diagnosis_reports_router
from src.api.routers.coaches import router as coaches_router
# Feature-017: videos.py 已下线 —— /videos/classifications* 端点并入 classifications.py
from src.api.routers.teaching_tips import router as teaching_tips_router
from src.api.routers.standards import router as standards_router
# Feature-017: diagnosis.py 已下线 —— 同步 POST /diagnosis 端点并入异步 /api/v1/tasks/diagnosis
from src.api.routers.admin import router as admin_router
from src.api.routers.task_channels import router as task_channels_router
from src.api.routers.extraction_jobs import router as extraction_jobs_router
from src.api.routers.video_preprocessing import router as video_preprocessing_router
# Feature-018 — 三阶段业务总览
from src.api.routers.business_workflow import router as business_workflow_router
# Feature-021 — 视频内容清洗
from src.api.routers.curation_jobs import router as curation_jobs_router
from src.api.routers.curation_stats import router as curation_stats_router
# Feature-022 — 内容审核工作台
from src.api.routers.content_reviews import router as content_reviews_router
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

    # Feature-021 (T048): 在 API 启动期加载并校验当前最新清洗规范.
    # 目的是 fail-fast 把"线上规范文件错"的发现时机从"第一条清洗任务"提前到
    # "API 启动"——但**不阻断 API 启动**：失败时只打 critical 日志，让运维仍能
    # 走 /admin 应急、查询 / 重启 等通道。一旦清洗任务真的提交，curation_service
    # 会再做一次同样的 load + 校验，第二道闸门把住。
    try:
        from src.services.curation.rubric_loader import latest_version, load
        rubric = load(latest_version())
        logger.info(
            "F-021 curation rubric loaded at startup: version=%s rules=%d",
            rubric.version, len(rubric.data.get("rules", {})),
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft on purpose
        logger.critical(
            "F-021 curation rubric failed to load at startup: %s "
            "(API will start; clean curation submissions will be rejected with "
            "RUBRIC_INVALID / RUBRIC_VERSION_NOT_FOUND until fixed)",
            exc,
        )

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
    # Feature-020 — 运动员视频素材归集与分类
    app.include_router(athlete_classifications_router, prefix="/api/v1")
    # Feature-020 — 运动员预处理 / 诊断 4 个端点
    app.include_router(athlete_tasks_router, prefix="/api/v1")
    # Feature-020 US5 — 运动员诊断报告聚合查询
    app.include_router(diagnosis_reports_router, prefix="/api/v1")
    app.include_router(standards_router, prefix="/api/v1")
    # Feature-017: diagnosis_router 已删除，同步 POST /diagnosis 端点下线
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(task_channels_router, prefix="/api/v1")
    app.include_router(extraction_jobs_router, prefix="/api/v1")
    app.include_router(video_preprocessing_router, prefix="/api/v1")
    # Feature-018 — 三阶段业务总览接口（US1）
    app.include_router(business_workflow_router, prefix="/api/v1")
    # Feature-021 — 视频内容清洗（POST /tasks/curation + GET /curation-jobs/{job_id}）
    app.include_router(curation_jobs_router, prefix="/api/v1")
    # Feature-021 US5 — 聚合统计观测（GET /curation-stats）
    app.include_router(curation_stats_router, prefix="/api/v1")
    # Feature-022 — 内容审核工作台（5 EP：GET/POST /content-reviews* + GET/PATCH /admin/review-gate）
    app.include_router(content_reviews_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
