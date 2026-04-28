#!/usr/bin/env bash
# ============================================================================
# init_reset_all.sh — 测试阶段一键初始化脚本（彻底清空，非生产脚本）
# ----------------------------------------------------------------------------
# 适用场景：系统尚未上线，测试阶段需要回到裸库状态重新开始
# 执行步骤（幂等，可重复运行）：
#   1. 停服：pkill API + 5 个 Celery Worker（TERM → KILL 两段式，含 fork 子进程）
#   2. TRUNCATE 全部业务表（保留 alembic_version 迁移状态）+ 全库审计
#   3. 清空 Redis（Celery broker / result backend 残留）
#   4. 删除本地 artifact / 预处理缓存 / 旧日志
#   5. 重启 API + 5 个 Celery Worker（按章程启动命令）
#   6. 最终审计：进程列表 / API 健康检查 / 队列长度
#
# 不做：
#   - 不自动触发 COS 全量扫描（重建需要 POST /api/v1/classifications/scan）
#   - 不清空 alembic_version（保留迁移状态，无需再次 upgrade head）
#
# 使用方法：
#   bash specs/017-api-standardization/scripts/init_reset_all.sh
# ============================================================================

set -uo pipefail

PROJECT_ROOT="/data/charhuang/char_ai_coding/charhuang_pp_cn"
PYTHON="/opt/conda/envs/coaching/bin/python3.11"
UVICORN="/opt/conda/envs/coaching/bin/uvicorn"
CELERY="/opt/conda/envs/coaching/bin/celery"

cd "${PROJECT_ROOT}" || { echo "[FATAL] 项目目录不存在：${PROJECT_ROOT}"; exit 2; }

# ---------------------------------------------------------------------------
# Step 1/6  停服（两段式 pkill：TERM → KILL，含 fork 子进程）
# ---------------------------------------------------------------------------
echo "=============================================================="
echo " Step 1/6  停掉 API + 全部 Celery Worker（含 fork 子进程）"
echo "=============================================================="
echo "  停服前："
echo "    uvicorn : $(pgrep -f 'uvicorn src.api.main' | wc -l)"
echo "    celery  : $(pgrep -f 'celery.*src.workers.celery_app' | wc -l)"

# 先 TERM 一次
pkill -TERM -f "uvicorn src.api.main"                2>/dev/null || true
pkill -TERM -f "celery.*src.workers.celery_app"      2>/dev/null || true
sleep 3
# 还没退就 KILL -9（清 fork 子进程）
pkill -KILL -f "uvicorn src.api.main"                2>/dev/null || true
pkill -KILL -f "celery.*src.workers.celery_app"      2>/dev/null || true
sleep 2

echo "  停服后："
echo "    uvicorn : $(pgrep -f 'uvicorn src.api.main' | wc -l)   (应为 0)"
echo "    celery  : $(pgrep -f 'celery.*src.workers.celery_app' | wc -l)   (应为 0)"

# ---------------------------------------------------------------------------
# Step 2/6  TRUNCATE 所有业务表 + 全库审计
# ---------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo " Step 2/6  TRUNCATE 全部业务表（保留 alembic_version）"
echo "=============================================================="
"${PYTHON}" <<'PYEOF'
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.config import get_settings

# —— 全量业务表清单（按 feature 分组，便于核对）——
BUSINESS_TABLES = [
    # Feature-013 任务管道
    "analysis_tasks", "audio_transcripts", "coaching_advice", "teaching_tips",
    "expert_tech_points", "tech_semantic_segments", "athlete_motion_analyses",
    "diagnosis_reports", "diagnosis_dimension_results", "deviation_reports",
    "skill_executions",
    # 核心资产
    "coaches", "coach_video_classifications", "video_classifications",
    "tech_knowledge_bases", "tech_standards", "tech_standard_points",
    "skills", "reference_videos", "reference_video_segments",
    # Feature-014 KB 提取管道
    "extraction_jobs", "pipeline_steps", "kb_conflicts",
    # Feature-016 视频预处理
    "video_preprocessing_jobs", "video_preprocessing_segments",
    # Feature-013/017 通道配置
    "task_channel_configs",
]

async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    async with engine.begin() as conn:
        # 实际存在的表（避免对未迁移的表 TRUNCATE 报错）
        existing = {
            row[0] for row in (
                await conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                ))
            ).fetchall()
        }
        to_truncate = [t for t in BUSINESS_TABLES if t in existing]
        skipped     = [t for t in BUSINESS_TABLES if t not in existing]
        unknown     = sorted(existing - set(BUSINESS_TABLES) - {"alembic_version"})

        print(f"  将 TRUNCATE ({len(to_truncate)} 张)：")
        for t in to_truncate:
            print(f"      - {t}")
        if skipped:
            print(f"  [SKIP] 清单里但库中不存在的表：{skipped}")
        if unknown:
            print(f"  [WARN] 库中存在但不在脚本清单里的表：{unknown}")
            print(f"         请核对是否需要加入 BUSINESS_TABLES；本次不动。")

        if to_truncate:
            stmt = "TRUNCATE TABLE " + ", ".join(to_truncate) + " RESTART IDENTITY CASCADE"
            await conn.execute(text(stmt))
            print(f"  ✓ TRUNCATE 完成，PK 序列已归零")

        # 全库审计：所有业务表行数应全为 0
        all_business = sorted(existing - {"alembic_version"})
        non_empty = []
        for t in all_business:
            cnt = (await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar_one()
            if cnt > 0:
                non_empty.append((t, cnt))
        if non_empty:
            print("  [WARN] 以下表仍有数据：")
            for t, cnt in non_empty:
                print(f"      {t}: {cnt}")
        else:
            print(f"  ✓ 全库审计通过：{len(all_business)} 张业务表全部为空")

        # alembic_version 保留
        ver = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        print(f"  ✓ 保留 alembic_version = {ver}")
    await engine.dispose()

asyncio.run(main())
PYEOF
DB_RC=$?
if [[ ${DB_RC} -ne 0 ]]; then
    echo "[FATAL] 数据库清空阶段失败（rc=${DB_RC}），后续步骤已中止"
    exit ${DB_RC}
fi

# ---------------------------------------------------------------------------
# Step 3/6  清空 Redis
# ---------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo " Step 3/6  清空 Redis（Celery broker + result backend）"
echo "=============================================================="
"${PYTHON}" <<'PYEOF'
from src.config import get_settings
from urllib.parse import urlparse
import redis

settings = get_settings()
broker = getattr(settings, "celery_broker_url", None) or getattr(settings, "redis_url", None)
if not broker:
    print("  [WARN] 未找到 celery_broker_url / redis_url，跳过 Redis 清理")
else:
    u = urlparse(broker)
    db_num = int((u.path or "/0").lstrip("/") or 0)
    r = redis.Redis(host=u.hostname or "localhost", port=u.port or 6379,
                    db=db_num, password=u.password)
    keys_before = r.dbsize()
    r.flushdb()
    print(f"  ✓ Redis DB={db_num} 已清空（清理前 keys={keys_before}）")
PYEOF

# ---------------------------------------------------------------------------
# Step 4/6  清理本地 artifact / 预处理缓存 / 旧日志
# ---------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo " Step 4/6  清理本地 artifact / 预处理缓存 / 旧日志"
echo "=============================================================="
rm -rf "${PROJECT_ROOT}/.artifacts"                 2>/dev/null || true
rm -rf /tmp/preprocess_* /tmp/kb_extract_*          2>/dev/null || true
rm -rf /tmp/celery_*_worker.log /tmp/uvicorn.log    2>/dev/null || true
echo "  ✓ 本地缓存与旧日志已清理"

# ---------------------------------------------------------------------------
# Step 5/6  重启 API + 5 个 Celery Worker
# ---------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo " Step 5/6  重启 API + 5 个 Celery Worker"
echo "=============================================================="

setsid "${UVICORN}" src.api.main:app --host 0.0.0.0 --port 8080 \
    >> /tmp/uvicorn.log 2>&1 < /dev/null &

setsid "${CELERY}" -A src.workers.celery_app worker --loglevel=info --concurrency=1 \
    -Q classification -n classification_worker@%h \
    >> /tmp/celery_classification_worker.log 2>&1 < /dev/null &

setsid "${CELERY}" -A src.workers.celery_app worker --loglevel=info --concurrency=2 \
    -Q kb_extraction -n kb_extraction_worker@%h \
    >> /tmp/celery_kb_extraction_worker.log 2>&1 < /dev/null &

setsid "${CELERY}" -A src.workers.celery_app worker --loglevel=info --concurrency=2 \
    -Q diagnosis -n diagnosis_worker@%h \
    >> /tmp/celery_diagnosis_worker.log 2>&1 < /dev/null &

setsid "${CELERY}" -A src.workers.celery_app worker --loglevel=info --concurrency=1 \
    -Q default -n default_worker@%h \
    >> /tmp/celery_default_worker.log 2>&1 < /dev/null &

setsid "${CELERY}" -A src.workers.celery_app worker --loglevel=info --concurrency=3 \
    -Q preprocessing -n preprocessing_worker@%h \
    >> /tmp/celery_preprocessing_worker.log 2>&1 < /dev/null &

echo "  等待服务就绪（8 秒）..."
sleep 8

# ---------------------------------------------------------------------------
# Step 6/6  最终审计：进程 / API 健康检查 / 队列长度
# ---------------------------------------------------------------------------
echo ""
echo "=============================================================="
echo " Step 6/6  最终状态审计"
echo "=============================================================="
echo "  进程统计（期望：uvicorn 父进程=1；celery 父进程=5，含并发子进程总数会更多）："
echo "    uvicorn 实例 (父+子)  ：$(pgrep -f 'uvicorn src.api.main' | wc -l)"
echo "    celery  实例 (父+子)  ：$(pgrep -f 'celery.*src.workers.celery_app' | wc -l)"

echo ""
echo "  5 个 celery 父进程明细（应恰好 5 行，每队列一个）："
ps -ef | grep -E "celery.*src.workers.celery_app.*-n [a-z_]+_worker@" | grep -v grep \
    | awk '{for(i=1;i<=NF;i++){if($i=="-Q"){q=$(i+1)}if($i=="-n"){n=$(i+1)}if($i=="--concurrency"){c=$(i+1)}}print "    queue="q" name="n" concurrency="c" pid="$2}' \
    | sort -u

echo ""
echo "  API 健康检查："
curl -sS -o /dev/null -w "    HTTP %{http_code}  latency=%{time_total}s\n" \
    "http://localhost:8080/api/v1/classifications?page=1&page_size=1" \
    || echo "    [WARN] API 尚未就绪（可能还在启动，稍后可重试 curl）"

echo ""
echo "  Celery 队列长度（应全为 0）："
"${PYTHON}" <<'PYEOF'
from src.config import get_settings
from urllib.parse import urlparse
import redis
s = get_settings()
broker = getattr(s, "celery_broker_url", None) or getattr(s, "redis_url", None)
u = urlparse(broker)
r = redis.Redis(host=u.hostname or "localhost", port=u.port or 6379,
                db=int((u.path or "/0").lstrip("/") or 0), password=u.password)
for q in ("classification", "kb_extraction", "diagnosis", "default", "preprocessing"):
    print(f"    queue={q:<16s} length={r.llen(q)}")
PYEOF

echo ""
echo "=============================================================="
echo " ✅ 初始化完成，系统已就绪"
echo "=============================================================="
echo "下一步（可选）："
echo "  1. 触发 COS 全量扫描（重建 coach_video_classifications）："
echo "     curl -X POST http://localhost:8080/api/v1/classifications/scan"
echo "  2. 查看扫描进度："
echo "     curl http://localhost:8080/api/v1/classifications/scan/<task_id>"
