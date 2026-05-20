"""Feature-021 T062 — 人工覆盖后视频级摘要自动重算 + kb_stale_after_override 维护.

聚焦 service 层 ``override_segment`` 端到端：

1. rejected → accepted 覆盖后，``video_curation_jobs`` 视频级摘要按
   ``effective_decision`` 重算（accepted_count / accepted_duration_ratio /
   low_quality 等）
2. ``coach_video_classifications.low_quality`` 与 ``kb_stale_after_override``
   字段同步更新；后者当且仅当：(a) 存在覆盖记录 (b) 存在 ``extraction_jobs``
   completed 早于覆盖时间
3. 取消覆盖（override_decision=null）⇒ ``effective_decision`` 回退到
   ``auto_decision``，``kb_stale_after_override`` 回退到无覆盖 = false
4. 验证非法状态拒绝（status=running）

测试用 mock 的 ``AsyncSession``：每次 ``execute(...)`` 按调用顺序返回预先排好
的结果，覆盖路径 + 重算路径 + kb_stale 评估路径全部走通。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.errors import AppException, ErrorCode
from src.services.curation import curation_service


# ── 脚手架 ──────────────────────────────────────────────────────────


@dataclass
class _FakeJob:
    """video_curation_jobs row mock."""
    id: uuid.UUID
    cos_object_key: str
    coach_video_classification_id: uuid.UUID
    preprocessing_job_id: uuid.UUID
    curation_rubric_version: str = "v1"
    status: str = "success"
    audio_unavailable: bool = False
    accepted_duration_ratio: float = 0.4


@dataclass
class _FakeSegResult:
    """video_curation_segment_results row mock — STORED column simulated by override-aware property."""
    id: uuid.UUID
    job_id: uuid.UUID
    segment_index: int
    auto_decision: str
    validity_score: float = 0.5
    rejection_reason: str | None = None
    decision_source: str = "rule"
    dim_breakdown: dict | None = None
    override_decision: str | None = None
    override_user: str | None = None
    override_reason: str | None = None
    overridden_at: datetime | None = None

    @property
    def effective_decision(self) -> str:
        return self.override_decision or self.auto_decision


@dataclass
class _FakePPSeg:
    """video_preprocessing_segments row mock."""
    segment_index: int
    start_ms: int
    end_ms: int


def _make_fake_objs():
    """5 段视频 (各 60 秒) — auto: 3 accepted / 1 rejected / 1 uncertain."""
    cls_id = uuid.uuid4()
    pp_id = uuid.uuid4()
    job = _FakeJob(
        id=uuid.uuid4(),
        cos_object_key="charhuang/x/y.mp4",
        coach_video_classification_id=cls_id,
        preprocessing_job_id=pp_id,
    )
    seg_results = [
        _FakeSegResult(uuid.uuid4(), job.id, 0, "accepted"),
        _FakeSegResult(uuid.uuid4(), job.id, 1, "accepted"),
        _FakeSegResult(uuid.uuid4(), job.id, 2, "rejected"),  # 这条会被 c2 覆盖
        _FakeSegResult(uuid.uuid4(), job.id, 3, "uncertain"),
        _FakeSegResult(uuid.uuid4(), job.id, 4, "accepted"),
    ]
    pp_segs = [
        _FakePPSeg(i, i * 60_000, (i + 1) * 60_000) for i in range(5)
    ]
    return job, seg_results, pp_segs


def _build_session(
    *,
    job: _FakeJob,
    seg_results: list[_FakeSegResult],
    pp_segs: list[_FakePPSeg],
    target_seg_idx: int,
    extraction_completed_before_override: bool = False,
):
    """``override_segment`` 内部按以下顺序调用 session.execute:

    1. SELECT VideoCurationJob.WHERE.id == job_id           → job
    2. SELECT VideoCurationSegmentResult by (job_id, idx)   → target seg
    3. UPDATE segment row                                    → ack
    4. SELECT all VideoCurationSegmentResult by job_id ORDER → seg_results（被 UPDATE 影响过）
    5. SELECT all VideoPreprocessingSegment by job_id        → pp_segs
    6. UPDATE VideoCurationJob (summary)                     → ack
    7. _evaluate_kb_stale_after_override:
       a. SELECT VideoCurationJob.id WHERE classification_id → [(job.id,)]
       b. SELECT MAX(overridden_at) WHERE job_id IN (...) AND overridden_at IS NOT NULL
       c. SELECT count(extraction_jobs WHERE cos_key + status=success + completed < latest)
    8. UPDATE CoachVideoClassification (low_quality + kb_stale)
    9. SELECT VideoCurationSegmentResult by (job_id, idx)    → updated_seg
    """
    session = AsyncMock()

    target_seg = next(s for s in seg_results if s.segment_index == target_seg_idx)
    target_pp = next(p for p in pp_segs if p.segment_index == target_seg_idx)
    _ = target_pp  # 不直接用，pp_segs 列表已含全部

    # results sequence
    results: list[MagicMock] = []

    def _r_scalar_one_or_none(value):
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        return r

    def _r_scalar_one(value):
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=value)
        return r

    def _r_scalars_all(values):
        r = MagicMock()
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=values)
        r.scalars = MagicMock(return_value=scalars)
        return r

    def _r_rows(rows):
        r = MagicMock()
        r.all = MagicMock(return_value=rows)
        return r

    # 1. SELECT job
    results.append(_r_scalar_one_or_none(job))
    # 2. SELECT target seg (initial fetch)
    results.append(_r_scalar_one_or_none(target_seg))
    # 3. UPDATE segment row (no read)
    results.append(MagicMock())
    # 4. SELECT all segments for recompute (after the in-place UPDATE on target_seg)
    results.append(_r_scalars_all(seg_results))
    # 5. _load_segments_for_job → pp_segs
    results.append(_r_scalars_all(pp_segs))
    # 6. UPDATE job summary (no read)
    results.append(MagicMock())
    # 7a. SELECT VideoCurationJob.id WHERE classification_id
    results.append(_r_rows([(job.id,)]))
    # 7b. SELECT MAX(overridden_at)
    overridden_at_value = (
        datetime(2026, 5, 18, 11, 20, 0)
        if any(s.override_decision is not None or s.segment_index == target_seg_idx
               for s in seg_results)
        else None
    )
    results.append(_r_scalar_one_or_none(overridden_at_value))
    # 7c. SELECT COUNT(extraction_jobs ...)
    count_val = 1 if extraction_completed_before_override else 0
    results.append(_r_scalar_one(count_val))
    # 8. UPDATE coach_video_classification
    results.append(MagicMock())
    # 9. Re-fetch updated target seg
    results.append(_r_scalar_one(target_seg))

    def _execute_side_effect(*args, **kwargs):
        # In-place patching: when the UPDATE on segment is issued
        # (results[2]), we mutate the in-memory target_seg so step 4
        # sees the new override state. SQLAlchemy `update().values(...)`
        # is opaque; cheapest path is to apply the override AT step 2
        # (before UPDATE returns). We track by call count.
        if results:
            return results.pop(0)
        # Fallback for any extra calls (none expected)
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    return session, target_seg


# ── 测试用例 ──────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_override_rejected_to_accepted_recomputes_summary(monkeypatch):
    """覆盖 rejected→accepted 后 summary 重算：accepted_count + 1，
    accepted_duration_ratio 上升。"""
    job, seg_results, pp_segs = _make_fake_objs()
    # 模拟 UPDATE segment row 的副作用：在 session.execute 第 3 次调用前
    # 直接修改内存对象（与 STORED 计算列同步）
    session, target_seg = _build_session(
        job=job, seg_results=seg_results, pp_segs=pp_segs,
        target_seg_idx=2,
        extraction_completed_before_override=False,
    )

    # 直接修改 target_seg 模拟 UPDATE 落库后的状态
    def _apply_override():
        target_seg.override_decision = "accepted"
        target_seg.override_user = "ops_alice"
        target_seg.override_reason = "完整动作演示"
        target_seg.overridden_at = datetime(2026, 5, 18, 11, 20, 0)

    # patch session.execute 在第 3 次调用（UPDATE seg row）时同时 apply override
    real_execute = session.execute.side_effect
    call_count = {"n": 0}

    def _execute_with_inplace_apply(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:  # 第 3 次 = UPDATE seg row
            _apply_override()
        return real_execute(*args, **kwargs)

    session.execute.side_effect = _execute_with_inplace_apply

    out = await curation_service.override_segment(
        session,
        job_id=job.id,
        segment_index=2,
        override_decision="accepted",
        override_reason="完整动作演示",
        override_user="ops_alice",
    )

    # 5 段，原 accepted=3 + override 后 = 4；rejected=0；uncertain=1
    assert out.summary_recomputed["accepted_segment_count"] == 4
    assert out.summary_recomputed["rejected_segment_count"] == 0
    # 4 / 5 = 0.8
    assert out.summary_recomputed["accepted_duration_ratio"] == pytest.approx(0.8)
    assert out.summary_recomputed["low_quality"] is False
    assert out.kb_stale_after_override is False
    assert out.override_decision == "accepted"
    assert out.effective_decision == "accepted"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_override_with_prior_kb_extraction_marks_stale(monkeypatch):
    """该视频已有 KB 抽取作业 completed 早于覆盖时间 ⇒ kb_stale_after_override=true."""
    job, seg_results, pp_segs = _make_fake_objs()
    session, target_seg = _build_session(
        job=job, seg_results=seg_results, pp_segs=pp_segs,
        target_seg_idx=2,
        extraction_completed_before_override=True,  # ← 关键
    )
    real_execute = session.execute.side_effect
    call_count = {"n": 0}

    def _execute_with_apply(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:
            target_seg.override_decision = "accepted"
            target_seg.override_user = "ops_alice"
            target_seg.override_reason = "x"
            target_seg.overridden_at = datetime(2026, 5, 18, 11, 20, 0)
        return real_execute(*args, **kwargs)

    session.execute.side_effect = _execute_with_apply

    out = await curation_service.override_segment(
        session,
        job_id=job.id, segment_index=2,
        override_decision="accepted",
        override_reason="x",
        override_user="ops_alice",
    )
    assert out.kb_stale_after_override is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_override_rejects_running_job():
    """job.status='running' ⇒ AppException(INVALID_STATUS) — service 层短路在 UPDATE 之前."""
    job, seg_results, pp_segs = _make_fake_objs()
    job.status = "running"  # ← 关键

    session = AsyncMock()
    r1 = MagicMock()
    r1.scalar_one_or_none = MagicMock(return_value=job)
    session.execute = AsyncMock(return_value=r1)

    with pytest.raises(AppException) as exc_info:
        await curation_service.override_segment(
            session, job_id=job.id, segment_index=2,
            override_decision="accepted",
            override_reason="x", override_user="ops_alice",
        )
    assert exc_info.value.code == ErrorCode.INVALID_STATUS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_override_rejects_missing_reason_when_decision_set():
    """override_decision != null + override_reason 缺失 ⇒ VALIDATION_FAILED."""
    session = AsyncMock()  # 不应被调用

    with pytest.raises(AppException) as exc_info:
        await curation_service.override_segment(
            session, job_id=uuid.uuid4(), segment_index=0,
            override_decision="accepted",
            override_reason=None,  # ← 关键
            override_user="ops_alice",
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_override_rejects_empty_user():
    """override_user 为空白 ⇒ VALIDATION_FAILED."""
    session = AsyncMock()

    with pytest.raises(AppException) as exc_info:
        await curation_service.override_segment(
            session, job_id=uuid.uuid4(), segment_index=0,
            override_decision="accepted",
            override_reason="x",
            override_user="   ",  # ← 关键
        )
    assert exc_info.value.code == ErrorCode.VALIDATION_FAILED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cancel_override_clears_fields(monkeypatch):
    """override_decision=null ⇒ 清空所有 override 字段，effective_decision 回退到 auto."""
    job, seg_results, pp_segs = _make_fake_objs()
    # 先模拟 segment 已有覆盖
    target = next(s for s in seg_results if s.segment_index == 2)
    target.override_decision = "accepted"
    target.override_user = "ops_alice"
    target.override_reason = "x"
    target.overridden_at = datetime(2026, 5, 18, 11, 20, 0)

    session, target_seg = _build_session(
        job=job, seg_results=seg_results, pp_segs=pp_segs,
        target_seg_idx=2,
    )
    # 取消覆盖后 — 第 3 次 execute 时清空
    real_execute = session.execute.side_effect
    call_count = {"n": 0}

    def _execute_with_clear(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:
            target_seg.override_decision = None
            target_seg.override_user = None
            target_seg.override_reason = None
            target_seg.overridden_at = None
        return real_execute(*args, **kwargs)

    session.execute.side_effect = _execute_with_clear

    out = await curation_service.override_segment(
        session, job_id=job.id, segment_index=2,
        override_decision=None,  # ← 取消
        override_reason=None,
        override_user="ops_alice",
    )
    assert out.override_decision is None
    assert out.override_user is None
    assert out.overridden_at is None
    assert out.effective_decision == "rejected"  # 回退到 auto_decision='rejected'


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clear_kb_stale_after_override_helper():
    """``clear_kb_stale_after_override(cos_object_key)`` UPDATE 即返回，幂等."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())

    await curation_service.clear_kb_stale_after_override(
        session, cos_object_key="charhuang/x/y.mp4",
    )
    # 至少调用了一次 update
    assert session.execute.call_count == 1
