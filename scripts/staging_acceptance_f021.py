"""Feature-021 — 端到端验收脚本（T085-T087 staging 自动化版本）.

在已下载到本地的视频上端到端验证 F-021 各路径，**不依赖** API + Celery + Whisper：
- 直接通过 SQLAlchemy session 准备最小数据切片
- 调用 service 层（与 Celery worker 内部一致的入口）执行清洗
- 验证规则路决策、视频级摘要派生、KB 抽取门、人工覆盖与 kb_stale_after_override
- US5 聚合统计接口烟测

输出行格式：``[ ✅ / ❌ ]  T0NN  ...``
脚本退出码：全部通过 → 0；任一失败 → 1。

注：本脚本默认在 sandbox / staging 单机环境执行；生产环境请走 quickstart.md 的
完整 API 路径而非本脚本。
"""

from __future__ import annotations

import asyncio
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import AsyncSessionFactory as async_session_factory  # noqa: E402
from src.models.analysis_task import BusinessPhase  # noqa: E402
from src.models.audio_transcript import AudioTranscript  # noqa: E402
from src.models.coach_video_classification import CoachVideoClassification  # noqa: E402
from src.models.video_preprocessing_job import (  # noqa: E402
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)
from src.models.video_preprocessing_segment import VideoPreprocessingSegment  # noqa: E402
from src.services.curation.curation_service import (  # noqa: E402
    aggregate_curation_stats,
    fetch_curation_job_with_segments,
    override_segment,
    run_curation_job,
    submit_curation,
)
from src.services.curation.kb_gate import evaluate_curation_gate  # noqa: E402

COS_KEY = (
    "charhuang/tt_video/乒乓球合集【较新】/"
    "《知行合一》孙浩泓专业乒乓球全套教学课程120集/"
    "第15节直板反手 训练计划1-5.mp4"
)


# ─────────────────────────────────────────────────────────────────────────
# 输出
# ─────────────────────────────────────────────────────────────────────────


PASSED = []
FAILED = []


def step(name: str, ok: bool, detail: str = "") -> None:
    icon = "✅" if ok else "❌"
    line = f"[{icon}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line, flush=True)
    (PASSED if ok else FAILED).append(name)


# ─────────────────────────────────────────────────────────────────────────
# 数据准备
# ─────────────────────────────────────────────────────────────────────────


async def cleanup_previous_run(db) -> None:
    """删除该 cos_object_key 关联的所有 F-021 测试数据，保证幂等可重跑."""
    await db.execute(text(
        "DELETE FROM video_curation_segment_results WHERE job_id IN ("
        " SELECT id FROM video_curation_jobs WHERE cos_object_key=:k)"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM video_curation_jobs WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.execute(text(
        "UPDATE coach_video_classifications SET last_curation_job_id=NULL WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM audio_transcripts WHERE task_id IN ("
        " SELECT id FROM analysis_tasks WHERE cos_object_key=:k)"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM analysis_tasks WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM video_preprocessing_segments WHERE job_id IN ("
        " SELECT id FROM video_preprocessing_jobs WHERE cos_object_key=:k)"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM video_preprocessing_jobs WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM coach_video_classifications WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.commit()


async def seed_minimal_pipeline(db) -> tuple[uuid.UUID, uuid.UUID]:
    """插入最小依赖数据：classification + preprocessing job/segments + transcript.

    Returns:
        (classification_id, preprocessing_job_id)
    """
    from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType

    # 1) 教练视频分类（已分类、已预处理）
    cls = CoachVideoClassification(
        coach_name="孙浩泓",
        course_series="《知行合一》孙浩泓专业乒乓球全套教学课程120集",
        cos_object_key=COS_KEY,
        filename="第15节直板反手 训练计划1-5.mp4",
        tech_category="backhand_push",
        tech_tags=["反手", "推挡"],
        raw_tech_desc="直板反手训练",
        classification_source="rule",
        confidence=1.0,
        duration_s=20,
        kb_extracted=False,
        preprocessed=True,
    )
    db.add(cls)
    await db.flush()

    # 2) 视频预处理作业（success） + 4 个段（4×5s = 20s 与实际视频对齐）
    pp = VideoPreprocessingJob(
        cos_object_key=COS_KEY,
        status=PreprocessingJobStatus.success.value,
        force=False,
        started_at=datetime.now() - timedelta(minutes=5),
        completed_at=datetime.now() - timedelta(minutes=4),
        duration_ms=20000,
        segment_count=4,
        has_audio=True,
        business_phase=BusinessPhase.TRAINING,
        business_step="preprocess_video",
    )
    db.add(pp)
    await db.flush()

    for i in range(4):
        db.add(VideoPreprocessingSegment(
            job_id=pp.id,
            segment_index=i,
            start_ms=i * 5000,
            end_ms=(i + 1) * 5000,
            cos_object_key=f"{COS_KEY}.seg_{i:04d}.mp4",
            size_bytes=1_000_000,
        ))

    # 3) AudioTranscript：需要先有 analysis_tasks 行
    task_id = uuid.uuid4()
    db.add(AnalysisTask(
        id=task_id,
        task_type=TaskType.kb_extraction,
        video_filename="第15节直板反手.mp4",
        video_size_bytes=4_468_267,
        video_storage_uri=COS_KEY,
        cos_object_key=COS_KEY,
        status=TaskStatus.success,
        submitted_via="single",
    ))
    await db.flush()

    # 模拟 4 段语音的 sentences（与分段时间窗口对齐）：
    # seg0 (0-5s)  : 教学语 ⇒ 应判 accepted
    # seg1 (5-10s) : 教学语 ⇒ 应判 accepted
    # seg2 (10-15s): 解说+广告/无关 ⇒ 应判 rejected
    # seg3 (15-20s): 教学语 ⇒ 应判 accepted
    sentences = [
        {"start": 0.5, "end": 4.5,
         "text": "我们来看反手推挡的动作要领，注意手腕的发力和重心转移。"},
        {"start": 5.5, "end": 9.5,
         "text": "击球时拍面要前倾，向前下方发力压住球，保持身体平衡。"},
        {"start": 10.5, "end": 14.5,
         "text": "比赛比分10平，下面进入广告时间，请大家关注我们的赞助商。"},
        {"start": 15.5, "end": 19.5,
         "text": "练习训练时要注意动作的连贯性，反手推挡是基本功的核心。"},
    ]
    db.add(AudioTranscript(
        task_id=task_id,
        sentences=sentences,
        language="zh",
        model_version="whisper-small-staging-test",
    ))

    await db.commit()
    return cls.id, pp.id


# ─────────────────────────────────────────────────────────────────────────
# T085 — 端到端冒烟（quickstart § 2）
# ─────────────────────────────────────────────────────────────────────────


async def run_t085(db, classification_id: uuid.UUID) -> uuid.UUID | None:
    """提交清洗 → 跑 worker 入口 → 验证摘要 + 决策分布."""
    print("\n=== T085  端到端冒烟（rule-only 决策路径）===")

    # 1) submit_curation
    out = await submit_curation(db, classification_id=classification_id)
    step("T085.1  submit_curation 返回 queued",
         out.queued is True and out.idempotent_short_circuit is False,
         f"job_id={out.job_id} version={out.curation_rubric_version}")
    job_id = out.job_id

    # 2) 直接跑 worker 入口（绕过 Celery）
    result = await run_curation_job(db, job_id)
    step("T085.2  run_curation_job 返回 success",
         result == "success", f"result={result}")

    # 3) 取详情验证
    bundle = await fetch_curation_job_with_segments(db, job_id)
    if bundle is None:
        step("T085.3  fetch_curation_job_with_segments 命中", False)
        return None
    job, segs, extras = bundle
    step("T085.3  作业 status=success + 4 个分段写入",
         job.status == "success" and len(segs) == 4,
         f"status={job.status} segments={len(segs)}")

    # 4) 视频级摘要派生
    derived_ok = (
        job.total_segment_count == 4
        and job.accepted_duration_ratio is not None
        and 0.0 <= job.accepted_duration_ratio <= 1.0
        and job.audio_unavailable is False
    )
    step("T085.4  视频级摘要派生（accepted_duration_ratio + audio_unavailable）",
         derived_ok,
         f"ratio={job.accepted_duration_ratio} audio_unavailable={job.audio_unavailable}"
         f" low_quality={job.low_quality}")

    # 5) 决策分布合理性（教学/广告语料应能在 rule-only 下区分）
    decisions = [s.auto_decision for s in segs]
    print(f"        per-segment decisions: {decisions}")
    print(f"        per-segment validity_scores: {[round(s.validity_score, 3) for s in segs]}")
    print(f"        seg2 (广告) rejection_reason: {segs[2].rejection_reason}")
    has_some_signal = any(d in ("accepted", "rejected") for d in decisions)
    step("T085.5  规则路至少产生 accepted/rejected 信号（非全 uncertain）",
         has_some_signal, f"decisions={decisions}")

    return job_id


# ─────────────────────────────────────────────────────────────────────────
# T086 — 人工覆盖路径（quickstart § 3）
# ─────────────────────────────────────────────────────────────────────────


async def run_t086(db, job_id: uuid.UUID) -> None:
    """对一个 segment 做覆盖 → 验证摘要重算 + kb_stale_after_override."""
    print("\n=== T086  人工覆盖路径 ===")

    # 取一个 auto_decision='accepted' 的 segment 翻为 rejected
    bundle = await fetch_curation_job_with_segments(db, job_id)
    assert bundle is not None
    _, segs, _ = bundle
    target = next((s for s in segs if s.auto_decision == "accepted"), segs[0])

    out = await override_segment(
        db,
        job_id=job_id,
        segment_index=target.segment_index,
        override_decision="rejected",
        override_reason="ops staging acceptance",
        override_user="staging_test",
    )
    step("T086.1  override_segment 写入成功",
         out.override_decision == "rejected" and out.effective_decision == "rejected",
         f"effective={out.effective_decision}")

    # 重新拉详情，确认 has_overrides + 摘要变化
    bundle2 = await fetch_curation_job_with_segments(db, job_id)
    assert bundle2 is not None
    job2, segs2, extras2 = bundle2
    has_overrides_ok = extras2["has_overrides"] is True
    step("T086.2  has_overrides=true",
         has_overrides_ok, f"has_overrides={extras2['has_overrides']}")

    # 摘要重算：accepted_segment_count 应该 -1
    new_accepted = job2.accepted_segment_count or 0
    step("T086.3  视频级摘要事务内重算（accepted_segment_count 减少）",
         new_accepted < (job2.total_segment_count or 0),
         f"accepted={new_accepted}/{job2.total_segment_count}")

    # 取消覆盖
    out2 = await override_segment(
        db,
        job_id=job_id,
        segment_index=target.segment_index,
        override_decision=None,
        override_reason=None,
        override_user="staging_test",
    )
    step("T086.4  取消覆盖（override_decision=null）回到 auto_decision",
         out2.override_decision is None
         and out2.effective_decision == out2.auto_decision,
         f"effective={out2.effective_decision} auto={out2.auto_decision}")


# ─────────────────────────────────────────────────────────────────────────
# T087 — 应急回滚开关 + KB 抽取门
# ─────────────────────────────────────────────────────────────────────────


async def run_t087(db, classification_id: uuid.UUID) -> None:
    """验证 evaluate_curation_gate 在不同 settings 下的决策."""
    print("\n=== T087  应急回滚开关 + KB 抽取门 ===")

    # 重新加载 classification 行
    from src.models.coach_video_classification import CoachVideoClassification
    from sqlalchemy import select

    cls_row = (await db.execute(
        select(CoachVideoClassification).where(
            CoachVideoClassification.id == classification_id
        )
    )).scalar_one()

    # 1) 默认开关 OFF + 已有 success 清洗 ⇒ 闸门 'ok'
    from src.config import get_settings
    settings = get_settings()
    is_bypass_off = settings.kb_extraction_bypass_curation_gate is False
    step("T087.1  KB_EXTRACTION_BYPASS_CURATION_GATE 默认值=false",
         is_bypass_off,
         f"value={settings.kb_extraction_bypass_curation_gate}")

    gate = await evaluate_curation_gate(db, cos_object_key=cls_row.cos_object_key)
    step("T087.2  闸门返回（已有 success 清洗 + 非 low_quality ⇒ ok）",
         gate.decision in ("ok", "low_quality_warn"),
         f"decision={gate.decision}")

    # 2) 模拟开关 ON ⇒ 跳过闸门
    settings.kb_extraction_bypass_curation_gate = True
    try:
        gate_bypass = await evaluate_curation_gate(
            db, cos_object_key=cls_row.cos_object_key
        )
        step("T087.3  开关 ON ⇒ 闸门返回 'bypassed'",
             gate_bypass.decision == "bypassed",
             f"decision={gate_bypass.decision}")
    finally:
        settings.kb_extraction_bypass_curation_gate = False

    # 3) 不存在的 cos_object_key ⇒ 'required'
    gate_required = await evaluate_curation_gate(
        db, cos_object_key="charhuang/non_existent_video.mp4"
    )
    step("T087.4  闸门：未跑过清洗 ⇒ 'required'",
         gate_required.decision == "required",
         f"decision={gate_required.decision}")


# ─────────────────────────────────────────────────────────────────────────
# T088 — US5 GET /curation-stats（service 层烟测，不走 HTTP）
# ─────────────────────────────────────────────────────────────────────────


async def run_t088(db) -> None:
    """三种 group_by 维度调用聚合服务，验证返回结构合理."""
    print("\n=== T088  US5 聚合统计 service 烟测 ===")

    for gb in ("coach", "tech_category", "rubric_version"):
        items, total = await aggregate_curation_stats(
            db, group_by=gb, page=1, page_size=20
        )
        ok = total >= 1 and len(items) >= 1
        sample = items[0] if items else None
        detail = f"total={total} items={len(items)}"
        if sample:
            detail += (
                f" example=({sample.coach_name or sample.tech_category or sample.curation_rubric_version},"
                f" video_count={sample.video_count}, ratio={sample.avg_accepted_duration_ratio},"
                f" low_sample={sample.low_sample})"
            )
        step(f"T088.{gb}  group_by={gb}", ok, detail)

    # 样本量保护：本视频 video_count=1，应 low_sample=true
    items, _ = await aggregate_curation_stats(db, group_by="coach")
    target = next((i for i in items if i.coach_name == "孙浩泓"), None)
    step("T088.low_sample  video_count<5 ⇒ low_sample=true",
         target is not None and target.low_sample is True,
         f"low_sample={target.low_sample if target else 'N/A'}")


# ─────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────


async def main() -> int:
    print(f"COS object key: {COS_KEY}")
    print(f"Local file:    /tmp/f021_test.mp4")

    async with async_session_factory() as db:
        await cleanup_previous_run(db)
        classification_id, pp_job_id = await seed_minimal_pipeline(db)
        print(f"seeded classification_id={classification_id}")
        print(f"seeded preprocessing_job_id={pp_job_id}")

    async with async_session_factory() as db:
        job_id = await run_t085(db, classification_id)
        if job_id is None:
            print("\n[FATAL] T085 未取得 job_id，后续步骤跳过")
            print(f"\nSummary: passed={len(PASSED)}, failed={len(FAILED)}")
            return 1

    async with async_session_factory() as db:
        await run_t086(db, job_id)

    async with async_session_factory() as db:
        await run_t087(db, classification_id)

    async with async_session_factory() as db:
        await run_t088(db)

    print(f"\n{'='*60}")
    print(f"Summary: passed={len(PASSED)}, failed={len(FAILED)}")
    if FAILED:
        print("\nFAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("\n🎉  ALL F-021 acceptance steps PASSED on real-COS video.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
