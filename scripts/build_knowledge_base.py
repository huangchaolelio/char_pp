#!/usr/bin/env python3
"""build_knowledge_base.py — 离线构建专家技术知识库

从 COS 下载精选教学视频，运行完整处理流水线，将 ExpertTechPoint 写入数据库，
最终 approve 版本 1.0.0。

用法：
    cd /data/charhuang/charhuang_ai_coding/charhuang_pp_cn
    python3 scripts/build_knowledge_base.py

环境变量（从 .env 自动加载）：
    COS_SECRET_ID, COS_SECRET_KEY, COS_REGION, COS_BUCKET
    DATABASE_URL（支持 PostgreSQL 和 SQLite）

SQLite 快捷模式（无 PostgreSQL 时）：
    export DATABASE_URL="sqlite+aiosqlite:///./kb_build.sqlite3"
"""

import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ── 确保项目根在 sys.path ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ── 日志配置 ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("build_kb")

# ── 精选视频列表 ──────────────────────────────────────────────────────────────
# 选择正手拉球和反手推拨最具代表性的纯技术教学视频（非跑位/训练计划/发球）
SELECTED_VIDEOS = [
    # forehand_topspin
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第06节正手攻球.mp4",
        "action_type": "forehand_topspin",
        "notes": "正手攻球基础动作教学",
    },
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第27节正手连续拉.mp4",
        "action_type": "forehand_topspin",
        "notes": "正手连续拉球技术",
    },
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第58节正手起下旋.mp4",
        "action_type": "forehand_topspin",
        "notes": "正手起下旋完整发力动作",
    },
    # backhand_push
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第10节横板反手推拨.mp4",
        "action_type": "backhand_push",
        "notes": "横板反手推拨基础技术",
    },
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第60节反手起下旋.mp4",
        "action_type": "backhand_push",
        "notes": "反手起下旋完整动作",
    },
    {
        "cos_key": "charhuang/tt_video/乒乓球合集【较新】/《知行合一》孙浩泓专业乒乓球全套教学课程120集/第56节反手弹击.mp4",
        "action_type": "backhand_push",
        "notes": "反手弹击发力技术",
    },
]


# ── 数据库初始化 ──────────────────────────────────────────────────────────────

async def init_db(engine):
    """创建所有表（幂等）。"""
    # 注册所有模型
    import src.models  # noqa: F401 — side effect: register all mappers

    from src.db.session import Base
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import ARRAY

    db_url = str(engine.url)
    is_sqlite = "sqlite" in db_url

    if is_sqlite:
        # SQLite 兼容性：替换 PostgreSQL 专有类型和约束
        for table in Base.metadata.tables.values():
            for col in table.columns:
                if isinstance(col.type, ARRAY):
                    col.type = sa.JSON()
            # 移除 PostgreSQL 专有的 CHECK 约束（如 ~ 正则运算符）
            pg_constraints = [
                c for c in list(table.constraints)
                if hasattr(c, 'sqltext') and '~' in str(c.sqltext)
            ]
            for c in pg_constraints:
                table.constraints.discard(c)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified.")


# ── 单视频处理流水线 ──────────────────────────────────────────────────────────

def process_one_video(cos_key: str, action_hint: str, notes: str) -> dict:
    """
    同步处理单个视频：下载 → 验证 → 姿态估计 → 分割 → 分类 → 提取

    返回：
        {
            "task_id": UUID,
            "cos_key": str,
            "video_meta": VideoMeta,
            "extraction_results": list[ExtractionResult],
            "error": str | None,
        }
    """
    from src.services import cos_client
    from src.services.video_validator import validate_video, VideoQualityRejected
    from src.services.pose_estimator import estimate_pose
    from src.services.action_segmenter import segment_actions, frames_for_segment
    from src.services.action_classifier import classify_segment
    from src.services.tech_extractor import extract_tech_points

    task_id = uuid.uuid4()
    tmp_path = None
    result = {
        "task_id": task_id,
        "cos_key": cos_key,
        "video_meta": None,
        "extraction_results": [],
        "error": None,
    }

    try:
        # 1. 下载
        logger.info("[%s] Downloading %s ...", task_id, cos_key.split("/")[-1])
        tmp_path = cos_client.download_to_temp(cos_key)
        logger.info("[%s] Downloaded → %s (%.1f MB)", task_id, tmp_path, tmp_path.stat().st_size / 1e6)

        # 2. 视频质量验证
        video_meta = validate_video(tmp_path)
        result["video_meta"] = video_meta
        logger.info("[%s] Video: %s  fps=%.1f  dur=%.1fs", task_id,
                    video_meta.resolution_str, video_meta.fps, video_meta.duration_seconds)

        # 3. 姿态估计
        logger.info("[%s] Running pose estimation ...", task_id)
        all_frames = estimate_pose(tmp_path)
        if not all_frames:
            result["error"] = "NO_MOTION_DETECTED"
            return result
        logger.info("[%s] Pose: %d frames with keypoints", task_id, len(all_frames))

        # 4. 动作分割
        segments = segment_actions(all_frames)
        logger.info("[%s] Segmented: %d action segments", task_id, len(segments))
        if not segments:
            result["error"] = "NO_SEGMENTS"
            return result

        # 5. 分类 + 提取
        extraction_results = []
        for seg in segments:
            seg_frames = frames_for_segment(all_frames, seg)
            classified = classify_segment(seg_frames, seg)
            if classified.action_type == "unknown":
                continue
            ext = extract_tech_points(classified, all_frames)
            if ext.dimensions:
                extraction_results.append(ext)

        logger.info("[%s] Extracted %d action results (total dims: %d)",
                    task_id, len(extraction_results),
                    sum(len(e.dimensions) for e in extraction_results))
        result["extraction_results"] = extraction_results

    except Exception as exc:
        logger.error("[%s] Failed: %s", task_id, exc, exc_info=True)
        result["error"] = str(exc)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
            logger.info("[%s] Cleaned up temp file.", task_id)

    return result


# ── 数据库写入 ────────────────────────────────────────────────────────────────

async def persist_results(session, all_results: list[dict], approved_by: str = "build_script"):
    """将所有成功的提取结果写入数据库，创建并 approve 知识库版本 1.0.0。"""
    from sqlalchemy import select
    from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
    from src.services import knowledge_base_svc

    # 收集所有成功结果
    successful = [r for r in all_results if not r["error"] and r["extraction_results"]]
    if not successful:
        logger.error("No successful extraction results to persist.")
        return

    action_types_covered = list({
        ext.action_type
        for r in successful
        for ext in r["extraction_results"]
    })
    logger.info("Action types covered: %s", action_types_covered)

    async with session.begin():
        # 创建 draft KB 版本
        kb = await knowledge_base_svc.create_draft_version(
            session,
            action_types=action_types_covered,
            notes=f"Auto-built from {len(successful)} expert videos on {datetime.now(UTC).strftime('%Y-%m-%d')}",
        )
        logger.info("Created KB draft version: %s", kb.version)

        total_points = 0
        for r in successful:
            # 创建 AnalysisTask 记录
            vm = r["video_meta"]
            task = AnalysisTask(
                id=r["task_id"],
                task_type=TaskType.expert_video,
                status=TaskStatus.success,
                video_filename=r["cos_key"].split("/")[-1],
                video_size_bytes=0,
                video_storage_uri=r["cos_key"],
                video_fps=vm.fps if vm else None,
                video_resolution=vm.resolution_str if vm else None,
                video_duration_seconds=vm.duration_seconds if vm else None,
                knowledge_base_version=kb.version,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
            session.add(task)
            await session.flush()

            # 写入 tech points
            n = await knowledge_base_svc.add_tech_points(
                session, kb.version, r["task_id"], r["extraction_results"]
            )
            total_points += n
            logger.info("  %s → %d points added", r["cos_key"].split("/")[-1], n)

        logger.info("Total tech points inserted: %d", total_points)

    # Approve（单独事务）
    async with session.begin():
        kb, prev = await knowledge_base_svc.approve_version(
            session, kb.version, approved_by=approved_by,
            notes="Initial knowledge base built from expert teaching videos"
        )
    logger.info("✓ Knowledge base version %s APPROVED (prev: %s)", kb.version, prev)
    return kb


# ── 主函数 ────────────────────────────────────────────────────────────────────

async def main():
    # 加载 .env
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    # SQLite fallback（无 PostgreSQL 时）
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or "localhost" in db_url:
        sqlite_path = ROOT / "kb_build.sqlite3"
        db_url = f"sqlite+aiosqlite:///{sqlite_path}"
        os.environ["DATABASE_URL"] = db_url
        logger.warning("PostgreSQL not available — using SQLite: %s", sqlite_path)

    # 清除 lru_cache 以确保使用新的 DATABASE_URL
    from src.config import get_settings
    get_settings.cache_clear()

    # 构建 async engine（不使用 src.db.session 的全局 engine，避免连接参数冲突）
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}
    engine = create_async_engine(db_url, echo=False, connect_args=connect_args)

    await init_db(engine)

    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # ── 处理视频（同步，串行） ─────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Starting knowledge base build")
    logger.info("Videos to process: %d", len(SELECTED_VIDEOS))
    logger.info("=" * 60)

    all_results = []
    for i, video_cfg in enumerate(SELECTED_VIDEOS, 1):
        logger.info("\n[%d/%d] Processing: %s", i, len(SELECTED_VIDEOS),
                    video_cfg["cos_key"].split("/")[-1])
        result = process_one_video(
            video_cfg["cos_key"],
            video_cfg["action_type"],
            video_cfg["notes"],
        )
        all_results.append(result)
        status = "✓" if not result["error"] else f"✗ {result['error']}"
        pts = sum(len(e.dimensions) for e in result["extraction_results"])
        logger.info("[%d/%d] %s  → %d extraction results, %d dimensions",
                    i, len(SELECTED_VIDEOS), status,
                    len(result["extraction_results"]), pts)

    # 汇总
    succeeded = [r for r in all_results if not r["error"]]
    failed = [r for r in all_results if r["error"]]
    logger.info("\n" + "=" * 60)
    logger.info("Processing complete: %d succeeded, %d failed", len(succeeded), len(failed))
    if failed:
        for r in failed:
            logger.warning("  FAILED: %s — %s", r["cos_key"].split("/")[-1], r["error"])

    # ── 写入数据库 ─────────────────────────────────────────────────────────────
    async with SessionFactory() as session:
        kb = await persist_results(session, all_results)

    if kb:
        logger.info("\n✓ Knowledge base v%s is now ACTIVE", kb.version)
        logger.info("  DB: %s", db_url)
    else:
        logger.error("Knowledge base build FAILED — no data written.")
        sys.exit(1)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
