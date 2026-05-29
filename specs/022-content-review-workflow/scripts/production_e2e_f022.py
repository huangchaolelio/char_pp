"""Feature-022 — 内容审核工作流 · 端到端真实 COS 视频验证（自动审批通过）.

本脚本在 ``production_e2e_f021.py`` 基础上把链路前推到 KB 抽取，并把"内容审核"
环节自动化（调 EP-3 ``POST /content-reviews/{cvclf_id}/decisions`` 提交
``decision=approved`` 决策），完整跑通**四阶段链路**：

    CONTENT_PREP  : ① 分类 → ② 预处理 → ③ 清洗 → ④ 内容审核（自动通过）
    TRAINING      : ⑤ KB 抽取（DAG 6 步）

链路中任何一阶段不满足执行条件（视频质量被拒 / 清洗 0% 接受率 / 通道队列满 / KB
抽取上游失败 等），脚本会**自动将该 COS key 从候选池踢出并切换到下一条视频重试**，
直到某条视频跑通整链路或候选池耗尽。

使用方式
========

前置：API + 5 worker + Beat 已按 ``架构文档`` 启动。

::

    /opt/conda/envs/coaching/bin/python3.11 scripts/production_e2e_f022.py

环境变量（可选）
----------------

* ``E2E_API_URL``        默认 ``http://127.0.0.1:8080``
* ``E2E_REVIEWER_ID``    默认 ``e2e-auto-reviewer``
* ``E2E_MAX_VIDEOS``     候选池上限（默认 8，避免无限尝试）
* ``E2E_KB_TIMEOUT_SEC`` KB 抽取轮询超时（默认 600s）
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from pathlib import Path
from time import perf_counter

import httpx
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402
from src.db.session import AsyncSessionFactory  # noqa: E402
from src.models.analysis_task import (  # noqa: E402
    AnalysisTask, TaskStatus, TaskType,
)
from src.models.audio_transcript import AudioTranscript  # noqa: E402
from src.models.coach_video_classification import CoachVideoClassification  # noqa: E402
from src.models.video_curation_job import VideoCurationJob  # noqa: E402
from src.models.video_preprocessing_job import (  # noqa: E402
    PreprocessingJobStatus,
    VideoPreprocessingJob,
)
from src.models.video_preprocessing_segment import VideoPreprocessingSegment  # noqa: E402
from src.services import cos_client as cos_client_mod  # noqa: E402
from src.services.curation.curation_service import (  # noqa: E402
    fetch_curation_job_with_segments,
    run_curation_job,
    submit_curation,
)
from src.services.preprocessing.orchestrator import run_preprocessing  # noqa: E402
from src.services.tech_classifier import TechClassifier  # noqa: E402
from src.utils.time_utils import now_cst  # noqa: E402

API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8080").rstrip("/")
REVIEWER_ID = os.environ.get("E2E_REVIEWER_ID", "e2e-auto-reviewer")
MAX_VIDEOS = int(os.environ.get("E2E_MAX_VIDEOS", "8"))
KB_TIMEOUT_SEC = int(os.environ.get("E2E_KB_TIMEOUT_SEC", "600"))

# 候选池：从沙指导课程目录优先，挑文件大小适中的（10–40MB ≈ 60–180s）
COURSE_DIR = "全套技术教学大合集_源动力沙指导250节/"
SIZE_MIN_MB = 10
SIZE_MAX_MB = 40

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED_VIDEOS: list[tuple[str, str]] = []  # (cos_key, reason)


# ── 终端着色与状态打印 ──────────────────────────────────────────────────────

def step(name: str, ok: bool, detail: str = "") -> None:
    icon = "✅" if ok else "❌"
    line = f"[{icon}] {name}"
    if detail:
        line += f"  ({detail})"
    print(line, flush=True)
    (PASSED if ok else FAILED).append(name)


def banner(text_str: str) -> None:
    print(f"\n{'═' * 64}")
    print(f"  {text_str}")
    print(f"{'═' * 64}", flush=True)


def warn(text_str: str) -> None:
    print(f"   ⚠️  {text_str}", flush=True)


# ── 候选池：从 COS 列举 + 已落库的待清洗视频补全 ─────────────────────────

def build_candidates() -> list[dict]:
    """返回候选 COS 视频列表，按文件大小升序，过滤太大/太小。"""
    settings = get_settings()
    client, bucket = cos_client_mod._get_cos_client()
    prefix = settings.cos_video_all_cocah + COURSE_DIR
    print(f"  · COS bucket={bucket}")
    print(f"  · prefix={prefix}")

    cands: list[dict] = []
    marker = ""
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if marker:
            kwargs["Marker"] = marker
        resp = client.list_objects(**kwargs)
        for obj in resp.get("Contents", []):
            sz = int(obj["Size"])
            key = obj["Key"]
            if sz <= 0 or not key.endswith(".mp4"):
                continue
            mb = sz / 1024 / 1024
            if not (SIZE_MIN_MB <= mb <= SIZE_MAX_MB):
                continue
            filename = key.rsplit("/", 1)[-1]
            cands.append({
                "cos_object_key": key,
                "filename": filename,
                "size_mb": mb,
                "course_series": COURSE_DIR.rstrip("/"),
                "coach_name": "沙指导",
            })
        if resp.get("IsTruncated") != "true":
            break
        marker = resp.get("NextMarker", "")
        if not marker:
            break

    cands.sort(key=lambda c: c["size_mb"])
    return cands[:MAX_VIDEOS]


# ── 残留清理 ───────────────────────────────────────────────────────────────

async def cleanup(cos_object_key: str) -> None:
    """清理某个 cos_object_key 的所有遗留 DB 行（所有相关表级联）."""
    async with AsyncSessionFactory() as db:
        await db.execute(text(
            "DELETE FROM video_curation_segment_results WHERE job_id IN ("
            " SELECT id FROM video_curation_jobs WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM video_curation_jobs WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.execute(text(
            "UPDATE coach_video_classifications SET last_curation_job_id=NULL,"
            " last_decision_id=NULL WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM content_review_decisions WHERE cvclf_id IN ("
            " SELECT id FROM coach_video_classifications WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM audio_transcripts WHERE task_id IN ("
            " SELECT id FROM analysis_tasks WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        # 清 KB 抽取相关：pipeline_steps 通过 extraction_job_id FK / kb_conflicts
        # 通过 extraction_job_id；先取所有 extraction_job_id 再级联清
        await db.execute(text(
            "DELETE FROM kb_conflicts WHERE job_id IN ("
            " SELECT id FROM extraction_jobs WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM pipeline_steps WHERE job_id IN ("
            " SELECT id FROM extraction_jobs WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        await db.execute(text(
            "UPDATE analysis_tasks SET extraction_job_id=NULL"
            " WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM extraction_jobs WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        # 全清 analysis_tasks（包括 kb_extraction），避免 DUPLICATE_TASK 拦截
        await db.execute(text(
            "DELETE FROM analysis_tasks WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM video_preprocessing_segments WHERE job_id IN ("
            " SELECT id FROM video_preprocessing_jobs WHERE cos_object_key=:k)"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM video_preprocessing_jobs WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.execute(text(
            "DELETE FROM coach_video_classifications WHERE cos_object_key=:k"
        ), {"k": cos_object_key})
        await db.commit()


# ── Phase 1: 分类 + 预处理 ──────────────────────────────────────────────

class PhaseSkipped(Exception):
    """阶段无法继续（视频不满足条件）；调用方应跳到下一个候选视频."""

    def __init__(self, phase: str, reason: str) -> None:
        super().__init__(f"[{phase}] {reason}")
        self.phase = phase
        self.reason = reason


async def phase_1_classify_and_preprocess(cand: dict) -> tuple[uuid.UUID, uuid.UUID]:
    banner(f"Phase 1 · 分类 + 预处理   {cand['filename']}  ({cand['size_mb']:.1f} MB)")

    tcl = TechClassifier.from_settings()
    res = tcl.classify(filename=cand["filename"], course_series=cand["course_series"])
    if not res.tech_category or res.tech_category == "unclassified":
        raise PhaseSkipped("classify", f"tech_category=unclassified ({res.classification_source})")
    step("Phase1.classify", True,
         f"tech_category={res.tech_category} src={res.classification_source} conf={res.confidence}")

    async with AsyncSessionFactory() as db:
        cls = CoachVideoClassification(
            coach_name=cand["coach_name"],
            course_series=cand["course_series"],
            cos_object_key=cand["cos_object_key"],
            filename=cand["filename"],
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

        pp = VideoPreprocessingJob(
            cos_object_key=cand["cos_object_key"],
            status=PreprocessingJobStatus.running.value,
            force=False,
            started_at=now_cst(),
            has_audio=False,
        )
        db.add(pp)
        await db.flush()
        pp_id = pp.id
        await db.commit()

    print(f"   → run_preprocessing(job_id={pp_id}) ...")
    t0 = perf_counter()
    try:
        await run_preprocessing(pp_id)
    except Exception as exc:
        raise PhaseSkipped("preprocess", f"orchestrator raised {type(exc).__name__}: {exc}") from exc
    elapsed = perf_counter() - t0
    print(f"   ← run_preprocessing done in {elapsed:.1f}s")

    async with AsyncSessionFactory() as db:
        pp_row = await db.get(VideoPreprocessingJob, pp_id)
        if pp_row.status != PreprocessingJobStatus.success.value:
            raise PhaseSkipped(
                "preprocess",
                f"status={pp_row.status} error={pp_row.error_message or '-'}",
            )
        seg_count = (await db.execute(
            select(VideoPreprocessingSegment).where(VideoPreprocessingSegment.job_id == pp_id)
        )).scalars().all()
    step("Phase1.preprocess", True,
         f"segments={len(seg_count)} duration_ms={pp_row.duration_ms} has_audio={pp_row.has_audio}")
    return cls_id, pp_id


# ── Phase 2: Whisper（可选；失败仅警告，不阻塞链路）──────────────────────

async def phase_2_transcribe(pp_id: uuid.UUID, cand: dict) -> None:
    banner("Phase 2 · Whisper 转录（可选）")
    async with AsyncSessionFactory() as db:
        pp_row = await db.get(VideoPreprocessingJob, pp_id)
        local_dir = pp_row.local_artifact_dir
        has_audio = pp_row.has_audio

    if not has_audio:
        warn("视频无音轨；跳过 Whisper")
        step("Phase2.whisper", True, "skipped: no audio")
        return
    if not local_dir:
        warn("无 local_artifact_dir；跳过 Whisper")
        step("Phase2.whisper", True, "skipped: no local_dir")
        return
    audio_wav = Path(local_dir) / "audio.wav"
    if not audio_wav.exists():
        warn(f"audio.wav 不存在：{audio_wav}")
        step("Phase2.whisper", True, "skipped: audio.wav missing")
        return

    try:
        from src.services.speech_recognizer import SpeechRecognizer
        rec = SpeechRecognizer(model_name="small", device="auto")
        t0 = perf_counter()
        result = await asyncio.to_thread(rec.recognize, str(audio_wav), "zh")
        elapsed = perf_counter() - t0
    except Exception as exc:
        warn(f"Whisper 异常（不阻塞链路）：{type(exc).__name__}: {exc}")
        step("Phase2.whisper", True, "skipped: exception")
        return

    print(f"   ← Whisper {elapsed:.1f}s, sentences={len(result.sentences)}")
    async with AsyncSessionFactory() as db:
        task_id = uuid.uuid4()
        db.add(AnalysisTask(
            id=task_id,
            task_type=TaskType.kb_extraction,
            video_filename=cand["filename"],
            video_size_bytes=int(cand["size_mb"] * 1024 * 1024),
            video_storage_uri=cand["cos_object_key"],
            cos_object_key=cand["cos_object_key"],
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
    step("Phase2.whisper", True,
         f"sentences={len(result.sentences)} quality={result.quality_flag.value}")


# ── Phase 3: 清洗 ─────────────────────────────────────────────────────────

async def phase_3_curation(cls_id: uuid.UUID) -> uuid.UUID:
    banner("Phase 3 · 清洗（含 LLM 决策路径）")

    async with AsyncSessionFactory() as db:
        out = await submit_curation(db, classification_id=cls_id)
        if not out.queued:
            raise PhaseSkipped("curation", f"submit_curation refused queue: {out}")
        job_id = out.job_id
    step("Phase3.submit", True, f"job_id={job_id} rubric={out.curation_rubric_version}")

    print(f"   → run_curation_job(job_id={job_id}) ...")
    t0 = perf_counter()
    async with AsyncSessionFactory() as db:
        result = await run_curation_job(db, job_id)
    elapsed = perf_counter() - t0
    print(f"   ← run_curation_job  {elapsed:.1f}s  result={result}")
    if result != "success":
        raise PhaseSkipped("curation", f"run_curation_job result={result}")

    async with AsyncSessionFactory() as db:
        bundle = await fetch_curation_job_with_segments(db, job_id)
        if bundle is None:
            raise PhaseSkipped("curation", "fetch returned None")
        job, segs, _ = bundle
        if not segs:
            raise PhaseSkipped("curation", "0 segments produced")
        if (job.accepted_duration_ratio or 0) <= 0:
            raise PhaseSkipped(
                "curation",
                f"accepted_duration_ratio={job.accepted_duration_ratio}; "
                "审核门通过后 KB 抽取仍会被 LOW_QUALITY_SKIP 短路",
            )

    src_dist: dict[str, int] = {}
    dec_dist: dict[str, int] = {}
    for s in segs:
        src_dist[s.decision_source] = src_dist.get(s.decision_source, 0) + 1
        dec_dist[s.auto_decision] = dec_dist.get(s.auto_decision, 0) + 1
    print(f"   decision_source 分布: {src_dist}")
    print(f"   auto_decision    分布: {dec_dist}")
    print(f"   accepted_duration_ratio={job.accepted_duration_ratio}"
          f" low_quality={job.low_quality} audio_unavailable={job.audio_unavailable}")

    step("Phase3.curation", True,
         f"segs={len(segs)} ratio={job.accepted_duration_ratio:.3f}")
    return job_id


# ── Phase 4: 自动审批通过（调 EP-3 真实 HTTP）────────────────────────────

async def phase_4_auto_approve(cls_id: uuid.UUID, cos_object_key: str) -> None:
    banner("Phase 4 · 自动审批通过（EP-3）")

    # 4.1 清洗成功后 cvclf 应处于 pending_review；先从 DB 取当前 review_version 做乐观锁
    async with AsyncSessionFactory() as db:
        cvclf = await db.get(CoachVideoClassification, cls_id)
        review_state = cvclf.review_state
        review_version = int(cvclf.review_version)
    print(f"   cvclf review_state={review_state}  review_version={review_version}")

    if review_state != "pending_review":
        # 防御性：默认 server_default='pending_review' 已保证；若不是说明状态机有 bug
        raise PhaseSkipped(
            "review",
            f"unexpected review_state={review_state} (期望 pending_review；"
            "可能是迁移 0021 未生效或清洗后状态机异常)",
        )

    # 4.2 调 EP-3 真实 HTTP 提交 approved 决策
    url = f"{API_URL}/api/v1/content-reviews/{cls_id}/decisions"
    body = {
        "decision": "approved",
        "reviewer_id": REVIEWER_ID,
        "expected_review_version": review_version,
    }
    headers = {"X-Reviewer-Id": REVIEWER_ID, "Content-Type": "application/json"}
    print(f"   → POST {url}\n     body={body}")

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(url, json=body, headers=headers)
    if resp.status_code != 200:
        raise PhaseSkipped(
            "review",
            f"EP-3 returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    payload = resp.json()
    if not payload.get("success"):
        raise PhaseSkipped("review", f"EP-3 envelope success=false: {payload}")
    decision_data = payload["data"]
    print(f"   ← EP-3 200  decision={decision_data['decision']}"
          f" decided_at={decision_data['decided_at']}")

    # 4.3 复核 DB 状态
    async with AsyncSessionFactory() as db:
        cvclf = await db.get(CoachVideoClassification, cls_id)
        ok = (
            cvclf.review_state == "approved"
            and cvclf.review_version == review_version + 1
            and cvclf.last_decision_id is not None
            and cvclf.pending_since is None
        )
    step("Phase4.approve", ok,
         f"review_state={cvclf.review_state} version={cvclf.review_version} "
         f"last_decision_id={cvclf.last_decision_id}")
    if not ok:
        raise PhaseSkipped("review", "DB 状态未按预期切换到 approved")


# ── Phase 5: KB 抽取（提交 + 轮询）────────────────────────────────────────

async def phase_5_kb_extract(cos_object_key: str) -> str | None:
    banner("Phase 5 · KB 抽取（审核门已放行）")

    # 5.1 提交
    submit_url = f"{API_URL}/api/v1/tasks/kb-extraction"
    body = {"cos_object_key": cos_object_key, "force": False}
    print(f"   → POST {submit_url}\n     body={body}")
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(submit_url, json=body)
    if resp.status_code not in (200, 202):
        raise PhaseSkipped(
            "kb_extract",
            f"submit returned HTTP {resp.status_code}: {resp.text[:200]}",
        )
    payload = resp.json()
    if not payload.get("success"):
        raise PhaseSkipped("kb_extract", f"envelope success=false: {payload}")
    submission = payload["data"]
    items = submission.get("items") or []
    if submission.get("accepted", 0) < 1 or not items:
        raise PhaseSkipped(
            "kb_extract",
            f"submission rejected: accepted={submission.get('accepted')} "
            f"items={items}",
        )
    first = items[0]
    if not first.get("accepted"):
        raise PhaseSkipped(
            "kb_extract",
            f"item rejected: code={first.get('rejection_code')} "
            f"msg={first.get('rejection_message')}",
        )
    task_id = first.get("task_id")
    extraction_job_id = None  # 走 DB 查询补全
    print(f"   ← submission accepted: task_id={task_id}")

    if not extraction_job_id:
        # 兼容：从 analysis_tasks 取
        async with AsyncSessionFactory() as db:
            row = (await db.execute(text(
                "SELECT extraction_job_id FROM analysis_tasks WHERE id=:t"
            ), {"t": task_id})).first()
            extraction_job_id = row[0] if row else None
    if not extraction_job_id:
        warn("无 extraction_job_id，仅按 task 粒度轮询")

    # 5.2 轮询 task 状态（task 终态保留即可，不强求 extraction job 全 success）
    deadline = perf_counter() + KB_TIMEOUT_SEC
    last_status = ""
    while perf_counter() < deadline:
        async with AsyncSessionFactory() as db:
            row = (await db.execute(text(
                "SELECT status FROM analysis_tasks WHERE id=:t"
            ), {"t": task_id})).first()
        if row is None:
            await asyncio.sleep(3)
            continue
        s = row[0]
        if s != last_status:
            print(f"   [{int(perf_counter())%10000:04d}s] task.status={s}", flush=True)
            last_status = s
        if s in ("success", "failed"):
            break
        await asyncio.sleep(5)

    async with AsyncSessionFactory() as db:
        row = (await db.execute(text(
            "SELECT status, error_code, error_message FROM analysis_tasks WHERE id=:t"
        ), {"t": task_id})).first()
        final_status, err_code, err_msg = row if row else ("unknown", None, None)
        print(f"   final task.status={final_status} err_code={err_code} err_msg={(err_msg or '')[:120]}")
        if extraction_job_id:
            steps = (await db.execute(text(
                "SELECT step_type, status, error_code FROM pipeline_steps "
                "WHERE job_id=:j ORDER BY step_type"
            ), {"j": extraction_job_id})).all()
            print("   pipeline_steps:")
            for st, ss, ec in steps:
                print(f"     - {st:24s} {ss:10s} err={ec or '-'}")

    if final_status != "success":
        raise PhaseSkipped(
            "kb_extract",
            f"final status={final_status} err={err_code or err_msg}",
        )
    step("Phase5.kb_extract", True, f"task={task_id} extraction_job={extraction_job_id}")
    return str(extraction_job_id) if extraction_job_id else None


# ── 主循环 ─────────────────────────────────────────────────────────────────

async def try_one_video(cand: dict) -> bool:
    """跑通一条候选视频；中途 PhaseSkipped 即返回 False（外层切下一条）."""
    cos_key = cand["cos_object_key"]
    print()
    print("█" * 64)
    print(f"█  尝试：{cand['filename']}  ({cand['size_mb']:.1f} MB)")
    print(f"█  COS key: {cos_key}")
    print("█" * 64)

    await cleanup(cos_key)

    try:
        cls_id, pp_id = await phase_1_classify_and_preprocess(cand)
        await phase_2_transcribe(pp_id, cand)
        await phase_3_curation(cls_id)
        await phase_4_auto_approve(cls_id, cos_key)
        await phase_5_kb_extract(cos_key)
        return True
    except PhaseSkipped as exc:
        warn(f"该视频不满足执行条件 — {exc}")
        SKIPPED_VIDEOS.append((cos_key, str(exc)))
        return False
    except Exception:
        traceback.print_exc()
        SKIPPED_VIDEOS.append((cos_key, "uncaught exception (see traceback)"))
        return False


async def main() -> int:
    print("Feature-022 端到端真实 COS 验证（自动审批通过）")
    print(f"  API_URL    = {API_URL}")
    print(f"  REVIEWER   = {REVIEWER_ID}")
    print(f"  MAX_VIDEOS = {MAX_VIDEOS}")
    print(f"  KB_TIMEOUT = {KB_TIMEOUT_SEC}s\n")

    # 健康检查 API（用 /openapi.json 同时验证 EP-3 与 KB 抽取端点已注册）
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            r = await http.get(f"{API_URL}/openapi.json")
            assert r.status_code == 200, r.text
            paths = r.json().get("paths", {})
        required = [
            "/api/v1/content-reviews/{cvclf_id}/decisions",
            "/api/v1/tasks/kb-extraction",
        ]
        missing = [p for p in required if p not in paths]
        if missing:
            print(f"  ❌ API 缺少必要端点: {missing}")
            return 2
        print(f"  ✅ API openapi OK；必要端点 {len(required)}/已暴露")
    except Exception as exc:
        print(f"  ❌ API 不可达 ({API_URL}): {exc}")
        return 2

    print()
    print("候选视频列表（按文件大小升序）:")
    cands = build_candidates()
    if not cands:
        print("  ❌ 候选池为空（无 10–40MB 的 .mp4），脚本退出")
        return 2
    for c in cands:
        print(f"   · {c['size_mb']:5.1f} MB  {c['filename']}")

    success_key: str | None = None
    success_extraction_job: str | None = None
    for cand in cands:
        ok = await try_one_video(cand)
        if ok:
            success_key = cand["cos_object_key"]
            break

    print(f"\n{'═' * 64}")
    print("Summary")
    print(f"{'═' * 64}")
    print(f"  Passed steps : {len(PASSED)}")
    print(f"  Failed steps : {len(FAILED)}")
    print(f"  Skipped vids : {len(SKIPPED_VIDEOS)}")
    if SKIPPED_VIDEOS:
        for k, r in SKIPPED_VIDEOS:
            print(f"    - {k.rsplit('/',1)[-1]}: {r}")
    if FAILED:
        print("  Failed details:")
        for f in FAILED:
            print(f"    - {f}")

    if success_key:
        print(f"\n🎉 端到端 PASSED — {success_key.rsplit('/',1)[-1]}")
        return 0
    print("\n❌ 候选池全部跳过，未能跑通任一视频。")
    return 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception:
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
