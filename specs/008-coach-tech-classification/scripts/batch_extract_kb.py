"""批量提取教练视频知识库脚本 (Feature 008)

用法:
  python specs/008-coach-tech-classification/scripts/batch_extract_kb.py \
      --tech_category forehand_topspin \
      --batch_size 3 \
      --poll_interval 30

功能:
  1. 从 coach_video_classifications 查询指定技术类别的待处理视频
  2. 每批提交 batch_size 个到 POST /api/v1/tasks/expert-video
  3. 轮询等待这批全部完成（success/partial_success/failed/rejected）
  4. 成功的自动更新 kb_extracted=true，失败的记录跳过
  5. 输出最终统计
"""

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import psycopg2
import requests

TMP_DIR = Path("/tmp/coaching-advisor")
TMP_CLEANUP_THRESHOLD_GB = 10
TMP_STALE_MINUTES = 30  # 超过此时间未访问视为已完成，可清理


def get_tmp_dir_gb() -> float:
    """Return /tmp/coaching-advisor size in GB."""
    if not TMP_DIR.exists():
        return 0.0
    total = sum(f.stat().st_size for f in TMP_DIR.iterdir() if f.is_file())
    return total / (1024 ** 3)


def cleanup_stale_tmp_files() -> tuple[int, float]:
    """Delete files not accessed in the last TMP_STALE_MINUTES minutes.

    Returns (files_deleted, gb_freed).
    """
    if not TMP_DIR.exists():
        return 0, 0.0
    cutoff = time.time() - TMP_STALE_MINUTES * 60
    deleted = 0
    freed = 0.0
    for f in list(TMP_DIR.iterdir()):
        if not f.is_file():
            continue
        try:
            stat = f.stat()
            if stat.st_atime < cutoff:
                freed += stat.st_size
                f.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted, freed / (1024 ** 3)


def maybe_cleanup_tmp() -> None:
    """Check tmp dir size; clean up stale files if over threshold."""
    size_gb = get_tmp_dir_gb()
    if size_gb < TMP_CLEANUP_THRESHOLD_GB:
        return
    print(f"  🧹 /tmp/coaching-advisor 已达 {size_gb:.1f}GB (>{TMP_CLEANUP_THRESHOLD_GB}GB)，清理 {TMP_STALE_MINUTES}min 前的文件...")
    deleted, freed_gb = cleanup_stale_tmp_files()
    after_gb = get_tmp_dir_gb()
    print(f"     已删除 {deleted} 个文件，释放 {freed_gb:.1f}GB，剩余 {after_gb:.1f}GB")

API_BASE = "http://localhost:8080"
DB_URL = "postgresql://postgres:password@localhost:5432/coaching_db"


def get_pending_videos(tech_category: str) -> list[dict]:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id::text, cos_object_key, coach_name, filename
        FROM coach_video_classifications
        WHERE tech_category = %s AND kb_extracted = false
        ORDER BY coach_name, filename
        """,
        (tech_category,),
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"clf_id": r[0], "cos_object_key": r[1], "coach_name": r[2], "filename": r[3]}
        for r in rows
    ]


def submit_task(cos_object_key: str, action_type_hint: Optional[str] = None) -> Optional[str]:
    """Submit expert-video task, return task_id or None on error."""
    payload = {
        "cos_object_key": cos_object_key,
        "enable_audio_analysis": True,
        "audio_language": "zh",
    }
    if action_type_hint:
        payload["action_type_hint"] = action_type_hint
    try:
        resp = requests.post(
            f"{API_BASE}/api/v1/tasks/expert-video",
            json=payload,
            timeout=15,
        )
        if resp.status_code == 202:
            return resp.json()["task_id"]
        else:
            print(f"    [SUBMIT ERROR] {cos_object_key}: HTTP {resp.status_code} {resp.text[:120]}")
            return None
    except Exception as exc:
        print(f"    [SUBMIT EXCEPTION] {cos_object_key}: {exc}")
        return None


def poll_task_status(task_id: str) -> str:
    """Poll task status, return final status string."""
    try:
        resp = requests.get(f"{API_BASE}/api/v1/tasks/{task_id}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("status", "unknown")
        return "unknown"
    except Exception:
        return "unknown"


def mark_kb_extracted(clf_ids: list[str]) -> None:
    """Set kb_extracted=true for given classification record IDs."""
    if not clf_ids:
        return
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "UPDATE coach_video_classifications SET kb_extracted = true WHERE id = ANY(%s::uuid[])",
        (clf_ids,),
    )
    conn.commit()
    conn.close()


TERMINAL_STATUSES = {"success", "partial_success", "failed", "rejected"}


def process_batch(batch: list[dict], action_type_hint: Optional[str], poll_interval: int, max_wait: int = 1800) -> dict:
    """Submit a batch, wait for completion, return stats."""
    stats = {"success": [], "partial_success": [], "failed": [], "rejected": [], "submit_error": []}

    # Submit all in batch
    submitted = []  # (clf_id, cos_object_key, task_id)
    for item in batch:
        print(f"  → 提交: {item['coach_name']} / {item['filename']}")
        task_id = submit_task(item["cos_object_key"], action_type_hint)
        if task_id:
            submitted.append((item["clf_id"], item["cos_object_key"], task_id))
            print(f"    task_id={task_id}")
        else:
            stats["submit_error"].append(item["clf_id"])

    if not submitted:
        return stats

    # Poll until all done or timeout
    pending = {task_id: (clf_id, key) for clf_id, key, task_id in submitted}
    print(f"  ⏳ 等待 {len(pending)} 个任务完成 (轮询间隔 {poll_interval}s, 最长等待 {max_wait}s)...")
    deadline = time.time() + max_wait
    while pending:
        time.sleep(poll_interval)
        done_ids = []
        for task_id, (clf_id, key) in list(pending.items()):
            status = poll_task_status(task_id)
            if status in TERMINAL_STATUSES:
                done_ids.append(task_id)
                filename = key.rsplit("/", 1)[-1]
                print(f"    [{status.upper()}] {filename}")
                stats[status].append(clf_id)
        for tid in done_ids:
            del pending[tid]
        if pending:
            if time.time() >= deadline:
                print(f"    ⚠️  超时 ({max_wait}s)，强制跳过 {len(pending)} 个未完成任务")
                for clf_id, key in pending.values():
                    filename = key.rsplit("/", 1)[-1]
                    print(f"    [TIMEOUT] {filename}")
                    stats["failed"].append(clf_id)
                break
            print(f"    仍有 {len(pending)} 个任务运行中...")

    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tech_category", default="forehand_topspin")
    parser.add_argument("--batch_size", type=int, default=3)
    parser.add_argument("--poll_interval", type=int, default=30,
                        help="轮询间隔秒数（默认30s）")
    parser.add_argument("--action_type_hint", default=None,
                        help="传给 expert-video 的 action_type_hint（如 forehand_topspin）")
    parser.add_argument("--max_wait", type=int, default=1800,
                        help="每批最长等待时间秒数（默认1800s=30分钟）")
    args = parser.parse_args()

    videos = get_pending_videos(args.tech_category)
    total = len(videos)
    if total == 0:
        print(f"✅ {args.tech_category} 没有待处理视频，全部已提取。")
        return

    print(f"📋 共 {total} 个 {args.tech_category} 视频待处理，每批 {args.batch_size} 个\n")

    all_success_ids = []
    all_partial_ids = []
    all_failed = 0
    all_rejected = 0
    all_submit_error = 0

    for batch_num, start in enumerate(range(0, total, args.batch_size), 1):
        batch = videos[start : start + args.batch_size]
        print(f"━━━ 第 {batch_num} 批 [{start+1}-{start+len(batch)}/{total}] ━━━")
        stats = process_batch(batch, args.action_type_hint, args.poll_interval, args.max_wait)

        # Auto-update kb_extracted for success
        done_ids = stats["success"] + stats["partial_success"]
        if done_ids:
            mark_kb_extracted(done_ids)
            print(f"  ✅ 已标记 {len(done_ids)} 条 kb_extracted=true")

        all_success_ids += stats["success"]
        all_partial_ids += stats["partial_success"]
        all_failed += len(stats["failed"])
        all_rejected += len(stats["rejected"])
        all_submit_error += len(stats["submit_error"])

        # Auto-cleanup tmp dir if over threshold (after batch completes, safe to remove stale files)
        maybe_cleanup_tmp()
        print()

    print("=" * 50)
    print(f"🏁 批处理完成")
    print(f"   success        : {len(all_success_ids)}")
    print(f"   partial_success: {len(all_partial_ids)}")
    print(f"   failed (跳过)  : {all_failed}")
    print(f"   rejected (跳过): {all_rejected}")
    print(f"   submit_error   : {all_submit_error}")
    print(f"   kb_extracted=true 已更新: {len(all_success_ids) + len(all_partial_ids)}")


if __name__ == "__main__":
    main()
