"""Feature-021 — 生产路径端到端验收（真实 ffmpeg + Whisper + LLM + COS + PG）.

不同于 ``staging_acceptance_f021.py`` 用合成 transcript，本脚本走 **完整生产路径**：

  ① TechClassifier  从 cos_object_key 推断 tech_category
  ② 真 preprocessing.orchestrator.run_preprocessing  下载/转码/分段/上传 COS
  ③ 真 SpeechRecognizer  Whisper 转录 → audio_transcripts
  ④ 真 submit_curation + run_curation_job
       - 部分 segment 走规则路；得分 ∈ (0.3, 0.7) 的走 LLM fallback
       - 真 LlmClient (Venus/OpenAI) 决策
  ⑤ 验证：作业 success / 摘要派生 / KB 抽取门 / 聚合统计

测试视频：``127_正手拉上旋（14)_1080p.mp4``（沙指导课程，31MB / 122s / 1080p / 含音频）
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from time import perf_counter

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.session import AsyncSessionFactory  # noqa: E402
from src.models.analysis_task import AnalysisTask, BusinessPhase, TaskStatus, TaskType  # noqa: E402
from src.models.audio_transcript import AudioQualityFlag, AudioTranscript  # noqa: E402
from src.models.coach_video_classification import CoachVideoClassification  # noqa: E402
from src.models.video_curation_job import VideoCurationJob  # noqa: E402
from src.models.video_curation_segment_result import VideoCurationSegmentResult  # noqa: E402
from src.models.video_preprocessing_job import (  # noqa: E402
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)
from src.models.video_preprocessing_segment import VideoPreprocessingSegment  # noqa: E402
from src.services.curation.curation_service import (  # noqa: E402
    aggregate_curation_stats,
    fetch_curation_job_with_segments,
    run_curation_job,
    submit_curation,
)
from src.services.curation.kb_gate import evaluate_curation_gate  # noqa: E402
from src.services.tech_classifier import TechClassifier  # noqa: E402

COS_KEY = (
    "charhuang/tt_video/乒乓球合集【较新】/全套技术教学大合集_源动力沙指导250节/"
    "127_正手拉上旋（14)_1080p.mp4"
)
COURSE_SERIES = "全套技术教学大合集_源动力沙指导250节"
COACH_NAME = "沙指导"
FILENAME = "127_正手拉上旋（14)_1080p.mp4"

# ─────────────────────────────────────────────────────────────────────────


PASSED: list[str] = []
FAILED: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    icon = "✅" if ok else "❌"
    line = f"[{icon}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line, flush=True)
    (PASSED if ok else FAILED).append(name)


def banner(text: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {text}")
    print(f"{'═' * 60}", flush=True)


# ─────────────────────────────────────────────────────────────────────────


async def cleanup(db) -> None:
    await db.execute(text(
        "DELETE FROM video_curation_segment_results WHERE job_id IN ("
        " SELECT id FROM video_curation_jobs WHERE cos_object_key=:k)"
    ), {"k": COS_KEY})
    await db.execute(text(
        "DELETE FROM video_curation_jobs WHERE cos_object_key=:k"
    ), {"k": COS_KEY})
    await db.execute(text(
        "UPDATE coach_video_classifications SET last_curation_job_id=NULL"
        " WHERE cos_object_key=:k"
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


# ─────────────────────────────────────────────────────────────────────────
# Phase 1 — 分类 + 预处理（真路径）
# ─────────────────────────────────────────────────────────────────────────


async def phase_1_classify_and_preprocess() -> tuple[uuid.UUID, uuid.UUID]:
    banner("Phase 1 · 真分类 + 真 ffmpeg 预处理")

    # ① 调真 TechClassifier
    tcl = TechClassifier.from_settings()
    res = tcl.classify(filename=FILENAME, course_series=COURSE_SERIES)
    step("Phase1.classify  tech_category 推断成功",
         bool(res.tech_category) and res.tech_category != "unclassified",
         f"tech_category={res.tech_category} source={res.classification_source}"
         f" confidence={res.confidence}")

    # ② 写 coach_video_classifications
    async with AsyncSessionFactory() as db:
        cls = CoachVideoClassification(
            coach_name=COACH_NAME,
            course_series=COURSE_SERIES,
            cos_object_key=COS_KEY,
            filename=FILENAME,
            tech_category=res.tech_category,
            tech_tags=res.tech_tags or [],
            raw_tech_desc=res.raw_tech_desc,
            classification_source=res.classification_source,
            confidence=res.confidence,
            kb_extracted=False,
            preprocessed=False,
        )
        db.add(cls)
        await db.flush()
        cls_id = cls.id

        # ③ 创建 preprocessing job 行（业务字段由 _phase_step_hook 自动注入）
        from src.utils.time_utils import now_cst
        pp = VideoPreprocessingJob(
            cos_object_key=COS_KEY,
            status=PreprocessingJobStatus.running.value,
            force=False,
            started_at=now_cst(),
            has_audio=False,
        )
        db.add(pp)
        await db.flush()
        pp_id = pp.id
        await db.commit()

    # ④ 跑真预处理（同步等待）
    print(f"  → run_preprocessing(job_id={pp_id}) ...")
    t0 = perf_counter()
    from src.services.preprocessing.orchestrator import run_preprocessing
    await run_preprocessing(pp_id)
    elapsed = perf_counter() - t0
    print(f"  ← run_preprocessing 耗时 {elapsed:.1f}s")

    # ⑤ 复核结果
    async with AsyncSessionFactory() as db:
        pp_row = await db.get(VideoPreprocessingJob, pp_id)
        step("Phase1.preprocess  status=success",
             pp_row.status == PreprocessingJobStatus.success.value,
             f"status={pp_row.status} error={pp_row.error_message or '-'}")
        seg_count = (await db.execute(
            select(VideoPreprocessingSegment).where(
                VideoPreprocessingSegment.job_id == pp_id
            )
        )).scalars().all()
        n_seg = len(seg_count)
        step(f"Phase1.preprocess  生成 {n_seg} 个分段（默认 segment_duration_s=180）",
             n_seg >= 1,
             f"duration_ms={pp_row.duration_ms} has_audio={pp_row.has_audio}"
             f" audio_cos={pp_row.audio_cos_object_key}")

    return cls_id, pp_id


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 — 真 Whisper 转录
# ─────────────────────────────────────────────────────────────────────────


async def phase_2_transcribe(pp_id: uuid.UUID) -> None:
    banner("Phase 2 · 真 Whisper 转录")

    # 1) 取 preprocessing 落地的 audio.wav 路径
    async with AsyncSessionFactory() as db:
        pp_row = await db.get(VideoPreprocessingJob, pp_id)
        local_dir = pp_row.local_artifact_dir
    if not local_dir:
        step("Phase2.audio_path  preprocessing 未导出 audio_wav", False)
        return
    audio_wav = Path(local_dir) / "audio.wav"
    step("Phase2.audio_path  audio.wav 已生成",
         audio_wav.exists(),
         f"path={audio_wav} size={audio_wav.stat().st_size if audio_wav.exists() else 0}")

    if not audio_wav.exists():
        return

    # 2) 真 Whisper 转录
    from src.services.speech_recognizer import SpeechRecognizer
    print(f"  → Whisper transcribe(model=small, lang=zh) ...")
    t0 = perf_counter()
    rec = SpeechRecognizer(model_name="small", device="auto")
    result = await asyncio.to_thread(rec.recognize, str(audio_wav), "zh")
    elapsed = perf_counter() - t0
    print(f"  ← Whisper 耗时 {elapsed:.1f}s, 句子数={len(result.sentences)}")
    if result.sentences[:2]:
        for s in result.sentences[:3]:
            print(f"     [{s['start']:6.1f}-{s['end']:6.1f}] {s['text'][:80]}")

    step("Phase2.whisper  转录产出有效句子",
         len(result.sentences) > 0,
         f"quality={result.quality_flag.value} duration_s={result.total_duration_s}")

    # 3) 写 audio_transcripts（需先有 analysis_tasks 行）
    async with AsyncSessionFactory() as db:
        from src.utils.time_utils import now_cst
        task_id = uuid.uuid4()
        db.add(AnalysisTask(
            id=task_id,
            task_type=TaskType.kb_extraction,
            video_filename=FILENAME,
            video_size_bytes=int(audio_wav.stat().st_size * 30),
            video_storage_uri=COS_KEY,
            cos_object_key=COS_KEY,
            status=TaskStatus.success,
            submitted_via="single",
        ))
        await db.flush()

        db.add(AudioTranscript(
            task_id=task_id,
            language=result.language,
            model_version=result.model_version,
            total_duration_s=result.total_duration_s,
            snr_db=result.snr_db,
            quality_flag=result.quality_flag,
            fallback_reason=result.fallback_reason,
            sentences=result.sentences,
        ))
        await db.commit()

    step("Phase2.persist  audio_transcripts 行写入成功",
         True, f"task_id={task_id}")


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 — 真 LLM 决策路径的清洗
# ─────────────────────────────────────────────────────────────────────────


async def phase_3_curation(cls_id: uuid.UUID) -> uuid.UUID | None:
    banner("Phase 3 · 真 LLM 决策路径的清洗作业")

    async with AsyncSessionFactory() as db:
        out = await submit_curation(db, classification_id=cls_id)
        step("Phase3.submit  submit_curation queued",
             out.queued is True, f"job_id={out.job_id} version={out.curation_rubric_version}")
        job_id = out.job_id

    # 跑真 worker 入口（含真 LlmClient.from_settings 兜底）
    print(f"  → run_curation_job(job_id={job_id}) ...")
    t0 = perf_counter()
    async with AsyncSessionFactory() as db:
        result = await run_curation_job(db, job_id)
    elapsed = perf_counter() - t0
    print(f"  ← run_curation_job 耗时 {elapsed:.1f}s, result={result}")
    step("Phase3.run  run_curation_job 返回 success",
         result == "success", f"result={result}")

    if result != "success":
        return job_id

    # 复核：分段决策、决策来源分布
    async with AsyncSessionFactory() as db:
        bundle = await fetch_curation_job_with_segments(db, job_id)
        if bundle is None:
            step("Phase3.fetch  作业可查", False)
            return job_id
        job, segs, _ = bundle

    step("Phase3.fetch  作业 success + 分段写入",
         job.status == "success" and len(segs) > 0,
         f"status={job.status} segments={len(segs)}")

    # 决策来源统计：rule vs llm
    src_dist: dict[str, int] = {}
    dec_dist: dict[str, int] = {}
    for s in segs:
        src_dist[s.decision_source] = src_dist.get(s.decision_source, 0) + 1
        dec_dist[s.auto_decision] = dec_dist.get(s.auto_decision, 0) + 1
    print(f"  decision_source 分布: {src_dist}")
    print(f"  auto_decision    分布: {dec_dist}")
    print(f"  视频级摘要: ratio={job.accepted_duration_ratio}"
          f" low_quality={job.low_quality} audio_unavailable={job.audio_unavailable}"
          f" short={job.short_video}")

    step("Phase3.summary  视频级摘要派生（accepted_duration_ratio + audio_unavailable）",
         job.accepted_duration_ratio is not None
         and job.audio_unavailable is False,
         f"ratio={job.accepted_duration_ratio}")

    # 打印每个分段（只打前 6 条）
    for s in segs[:6]:
        text_preview = s.rejection_reason or "-"
        print(f"    seg{s.segment_index:02d} [{s.segment_start_ms/1000:6.1f}-"
              f"{s.segment_end_ms/1000:6.1f}s]"
              f"  auto={s.auto_decision:9s} score={s.validity_score:.2f}"
              f" src={s.decision_source:6s} reason={text_preview}")

    return job_id


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 — KB 抽取门 + 聚合统计
# ─────────────────────────────────────────────────────────────────────────


async def phase_4_gates_and_stats() -> None:
    banner("Phase 4 · KB 抽取门 + 聚合统计")

    async with AsyncSessionFactory() as db:
        gate = await evaluate_curation_gate(db, cos_object_key=COS_KEY)
    step("Phase4.gate  KB 抽取门返回（清洗已 success ⇒ ok / low_quality_warn）",
         gate.decision in ("ok", "low_quality_warn"),
         f"decision={gate.decision} ratio={gate.accepted_duration_ratio}"
         f" segs(a={gate.accepted_segment_count}, r={gate.rejected_segment_count},"
         f" u={gate.uncertain_segment_count}/{gate.total_segment_count})")

    # 聚合统计三维度
    async with AsyncSessionFactory() as db:
        for gb in ("coach", "tech_category", "rubric_version"):
            items, total = await aggregate_curation_stats(
                db, group_by=gb, page=1, page_size=20
            )
            sample = items[0] if items else None
            detail = f"total={total}"
            if sample:
                key = (sample.coach_name or sample.tech_category
                       or sample.curation_rubric_version)
                detail += (
                    f" example=({key}, video_count={sample.video_count},"
                    f" avg_ratio={sample.avg_accepted_duration_ratio},"
                    f" avg_score={sample.avg_validity_score},"
                    f" low_sample={sample.low_sample})"
                )
            step(f"Phase4.stats  group_by={gb}",
                 total >= 1 and len(items) >= 1, detail)


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────


async def main() -> int:
    print(f"COS_KEY: {COS_KEY}")

    async with AsyncSessionFactory() as db:
        await cleanup(db)

    cls_id, pp_id = await phase_1_classify_and_preprocess()
    print(f"  classification_id = {cls_id}")
    print(f"  preprocessing_id  = {pp_id}")

    await phase_2_transcribe(pp_id)
    job_id = await phase_3_curation(cls_id)
    if job_id is None:
        print(f"\nSummary: passed={len(PASSED)}, failed={len(FAILED)}")
        return 1
    await phase_4_gates_and_stats()

    print(f"\n{'═' * 60}")
    print(f"Summary: passed={len(PASSED)}, failed={len(FAILED)}")
    if FAILED:
        print("\nFAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"\n🎉  生产路径全流程 PASSED on real-COS video.")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
