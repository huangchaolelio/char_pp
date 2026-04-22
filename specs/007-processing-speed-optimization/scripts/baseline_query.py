"""
T002 基准查询脚本：查询最近成功任务的处理耗时
用法: python3.11 baseline_query.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "/data/charhuang/char_ai_coding/charhuang_pp_cn/src")

async def main():
    from db.session import get_db
    from sqlalchemy import text

    sql = text("""
        SELECT
            id,
            task_type,
            EXTRACT(EPOCH FROM (completed_at - started_at))::int AS elapsed_s,
            video_duration_seconds::int AS dur_s,
            completed_at
        FROM analysis_tasks
        WHERE status = 'success'
          AND started_at IS NOT NULL
          AND completed_at IS NOT NULL
        ORDER BY completed_at DESC
        LIMIT 10
    """)

    async for session in get_db():
        result = await session.execute(sql)
        rows = result.fetchall()
        print(f"{'id':36s}  {'type':20s}  {'dur_s':>6}  {'elapsed_s':>9}  completed_at")
        print("-" * 90)
        for row in rows:
            print(f"{str(row.id):36s}  {str(row.task_type):20s}  {row.dur_s or 0:>6}  {row.elapsed_s or 0:>9}  {row.completed_at}")
        break  # get_db is a generator, only need one session

if __name__ == "__main__":
    asyncio.run(main())
