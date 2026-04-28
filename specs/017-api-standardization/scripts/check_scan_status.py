#!/usr/bin/env python3.11
"""check_scan_status.py — 查询当前是否有 COS 扫描任务在执行.

输出 4 块信息到 stdout：
  [1] Redis 5 队列积压 + celery-task-meta / unacked key 计数
  [2] analysis_tasks 状态汇总 + coach_video_classifications 行数
  [3] celery inspect active / reserved（跨所有 worker）
  [4] default worker 日志末尾 40 行（扫描任务在该 worker 跑）

用法（不带参数）:
    /opt/conda/envs/coaching/bin/python3.11 \
        specs/017-api-standardization/scripts/check_scan_status.py \
        > .artifacts_init_log.txt 2>&1
"""

from __future__ import annotations

import asyncio
import subprocess
from urllib.parse import urlparse

import redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import get_settings


META_KEY_PATTERN = "celery-task-meta-*"
UNACKED_KEY_PATTERN = "unacked*"
QUEUES = ("classification", "kb_extraction", "diagnosis", "default", "preprocessing")


async def main() -> None:
    s = get_settings()

    # ─────────────────────────────────────────────────────────────
    # [1] Redis 队列积压
    # ─────────────────────────────────────────────────────────────
    print("=" * 60)
    print(" [1] Redis 队列积压 + 关键 key 计数")
    print("=" * 60)
    broker = getattr(s, "celery_broker_url", None) or getattr(s, "redis_url", None)
    u = urlparse(broker)
    r = redis.Redis(
        host=u.hostname or "localhost",
        port=u.port or 6379,
        db=int((u.path or "/0").lstrip("/") or 0),
        password=u.password,
    )
    for q in QUEUES:
        print(f"   queue={q:<16s} backlog={r.llen(q)}")

    meta_keys = len(r.keys(META_KEY_PATTERN))
    unacked_keys = len(r.keys(UNACKED_KEY_PATTERN))
    print(f"   {META_KEY_PATTERN:<24s} count = {meta_keys}")
    print(f"   {UNACKED_KEY_PATTERN:<24s} count = {unacked_keys}")

    # ─────────────────────────────────────────────────────────────
    # [2] analysis_tasks 表汇总
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(" [2] analysis_tasks 状态汇总 + 扫描产物计数")
    print("=" * 60)
    eng = create_async_engine(s.database_url, pool_size=1, max_overflow=0)
    async with eng.begin() as c:
        rows = (await c.execute(text(
            "SELECT task_type, status, COUNT(*) "
            "FROM analysis_tasks "
            "GROUP BY task_type, status "
            "ORDER BY task_type, status"
        ))).fetchall()
        if not rows:
            print("   analysis_tasks 表为空（无任何任务记录）")
        else:
            print(f"   {'task_type':<20s} {'status':<14s} count")
            for row in rows:
                tt = row[0] if row[0] is not None else "<null>"
                st = row[1] if row[1] is not None else "<null>"
                print(f"   {tt:<20s} {st:<14s} {row[2]}")

        rows2 = (await c.execute(text(
            "SELECT id, task_type, status, submitted_via, parent_scan_task_id, "
            "       created_at "
            "FROM analysis_tasks "
            "ORDER BY created_at DESC NULLS LAST LIMIT 10"
        ))).fetchall()
        print()
        print(f"   最近 10 条任务：{len(rows2)} 行")
        for row in rows2:
            print(
                f"     id={row[0]}  type={row[1]}  status={row[2]}  "
                f"via={row[3]}  parent_scan={row[4]}  ct={row[5]}"
            )

        n_cvc = (await c.execute(
            text("SELECT COUNT(*) FROM coach_video_classifications")
        )).scalar_one()
        n_coach = (await c.execute(text("SELECT COUNT(*) FROM coaches"))).scalar_one()
        print()
        print(f"   coach_video_classifications = {n_cvc}")
        print(f"   coaches                      = {n_coach}")
    await eng.dispose()

    # ─────────────────────────────────────────────────────────────
    # [3] celery inspect active + reserved
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(" [3] celery inspect active / reserved（跨所有 worker）")
    print("=" * 60)
    celery_bin = "/opt/conda/envs/coaching/bin/celery"
    for sub in ("active", "reserved"):
        print(f"--- {sub} ---")
        try:
            out = subprocess.check_output(
                [celery_bin, "-A", "src.workers.celery_app",
                 "inspect", sub, "--timeout=3"],
                stderr=subprocess.STDOUT, timeout=10,
            ).decode("utf-8", errors="replace")
            print(out.strip() or "(empty)")
        except Exception as e:
            print(f"   [WARN] {e}")

    # ─────────────────────────────────────────────────────────────
    # [4] default worker 日志末尾
    # ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(" [4] default worker 日志末尾 40 行（scan_cos_videos 在这儿跑）")
    print("=" * 60)
    try:
        out = subprocess.check_output(
            ["tail", "-40", "/tmp/celery_default_worker.log"],
            stderr=subprocess.STDOUT, timeout=5,
        ).decode("utf-8", errors="replace")
        print(out.strip() or "(empty)")
    except Exception as e:
        print(f"   [WARN] 读取日志失败：{e}")


if __name__ == "__main__":
    asyncio.run(main())
