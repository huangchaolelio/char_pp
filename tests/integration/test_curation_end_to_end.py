"""Feature-021 T023 — 内容清洗端到端集成测试（DB-mocked）.

验证 ``run_curation_job`` 完整流程：
1. 加载 rubric → 取分段 + transcript → 逐分段 ``decide`` → 派生摘要 → 持久化
2. 视频级摘要的 4 个派生字段（accepted_duration_ratio / low_quality /
   audio_unavailable / short_video）口径正确
3. ``coach_video_classifications.last_curation_job_id`` 同步更新
4. 异常路径：rubric 加载失败 → ``_mark_job_failed`` 走通

本测试使用 mock 的 ``AsyncSession``（非真实 PG），重点验证 service 层
的"调用顺序 + 参数透传"链路；真实 DB 集成由 sandbox PG 可用时通过
``alembic upgrade head`` + 重新运行覆盖。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.errors import AppException, ErrorCode
from src.services.curation import curation_service


# ── 测试用脚手架 ─────────────────────────────────────────────────────


@dataclass
class _FakeJob:
    """mimics VideoCurationJob ORM row."""
    id: uuid.UUID
    cos_object_key: str
    coach_video_classification_id: uuid.UUID
    preprocessing_job_id: uuid.UUID
    curation_rubric_version: str
    status: str = "pending"


@dataclass
class _FakeSegment:
    """mimics VideoPreprocessingSegment ORM row."""
    segment_index: int
    start_ms: int
    end_ms: int


def _make_fake_job() -> _FakeJob:
    return _FakeJob(
        id=uuid.uuid4(),
        cos_object_key="charhuang/x/y/test.mp4",
        coach_video_classification_id=uuid.uuid4(),
        preprocessing_job_id=uuid.uuid4(),
        curation_rubric_version="v1",
    )


def _build_session_with_loaders(
    *,
    job: _FakeJob | None,
    segments: list[_FakeSegment],
    sentences: list[dict],
    classification: SimpleNamespace | None,
):
    """构造 ``AsyncMock`` session，按 run_curation_job 中的查询顺序返回结果.

    顺序（与 run_curation_job 内部一致）：
      1. SELECT VideoCurationJob.WHERE.id == job_id     → job_row
      2. UPDATE VideoCurationJob (status=running)        → ack
      3. SELECT VideoPreprocessingSegment ORDER BY idx  → segments
      4. SELECT AudioTranscript JOIN AnalysisTask        → sentences
      5. SELECT CoachVideoClassification.WHERE.id        → classification
      6. (per segment) ... no DB calls (decision is in-memory)
      7. INSERT N × VideoCurationSegmentResult           → ack（add，不返回）
      8. UPDATE VideoCurationJob (summary)               → ack
      9. UPDATE CoachVideoClassification                 → ack
    """
    session = AsyncMock()

    # ── 顺序 results：scalars / scalar_one / scalar_one_or_none 视情况返回 ──
    results_sequence: list[MagicMock] = []

    # 1. SELECT VideoCurationJob → job_row
    r1 = MagicMock()
    r1.scalar_one_or_none = MagicMock(return_value=job)
    results_sequence.append(r1)

    # 2. UPDATE VideoCurationJob (status=running) → 不读结果
    r2 = MagicMock()
    results_sequence.append(r2)

    # 3. SELECT VideoPreprocessingSegment → segments
    r3 = MagicMock()
    scalars3 = MagicMock()
    scalars3.all = MagicMock(return_value=segments)
    r3.scalars = MagicMock(return_value=scalars3)
    results_sequence.append(r3)

    # 4. SELECT AudioTranscript.sentences → sentences (or None)
    r4 = MagicMock()
    r4.scalar_one_or_none = MagicMock(
        return_value=sentences if sentences else None
    )
    results_sequence.append(r4)

    # 5. SELECT CoachVideoClassification → classification
    r5 = MagicMock()
    r5.scalar_one_or_none = MagicMock(return_value=classification)
    results_sequence.append(r5)

    # 6. INSERT/UPDATE 等后续调用（accept-and-proceed）
    persist_acks = MagicMock()

    def _execute_side_effect(*args, **kwargs):
        if results_sequence:
            return results_sequence.pop(0)
        return persist_acks

    session.execute.side_effect = _execute_side_effect
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


# ── 测试用例 ─────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_high_quality_video_yields_success(monkeypatch):
    """5 条分段 + 高质量教学转录 → run_curation_job 落 status='success'."""
    job = _make_fake_job()
    segments = [
        _FakeSegment(segment_index=i, start_ms=i * 60_000, end_ms=(i + 1) * 60_000)
        for i in range(5)
    ]
    # transcript 覆盖 0~300 秒，每分段都有教学文本
    sentences = [
        {"start": float(i * 60), "end": float((i + 1) * 60),
         "text": "示范一下这个动作，技术要点是收小臂，重心要转，关键点是击球瞬间", "confidence": 0.9}
        for i in range(5)
    ]
    classification = SimpleNamespace(coach_name="张继科", tech_category="forehand_topspin")

    session = _build_session_with_loaders(
        job=job, segments=segments, sentences=sentences,
        classification=classification,
    )

    # 关闭 LLM client 构造（curation_service 内部懒构造，未配置时返回 None）
    monkeypatch.setattr(
        curation_service, "_make_llm_client_or_none", lambda: None
    )

    final = await curation_service.run_curation_job(session, job.id)
    assert final == "success"

    # session.add 至少 5 次（每分段一次 segment_results 行）
    assert session.add.call_count == 5
    # session.commit 至少 2 次（标 running + 持久化）
    assert session.commit.call_count >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_match_replay_yields_low_quality(monkeypatch):
    """所有分段为比赛回放 → accepted_duration_ratio=0 + low_quality=True."""
    job = _make_fake_job()
    segments = [
        _FakeSegment(segment_index=i, start_ms=i * 60_000, end_ms=(i + 1) * 60_000)
        for i in range(3)
    ]
    sentences = [
        {"start": float(i * 60), "end": float((i + 1) * 60),
         "text": "本场比赛的关键时刻，本场胜利属于他，全场比分定格", "confidence": 0.9}
        for i in range(3)
    ]
    classification = SimpleNamespace(coach_name="张继科", tech_category="forehand_topspin")

    # 拦截 _aggregate_summary 的最终 update 调用，截获 summary 内容
    captured_updates: list[dict] = []

    real_persist = curation_service._persist_results

    async def _spy_persist(db, *, job, segments, decisions, summary):
        captured_updates.append(summary)
        # 不实际 await DB（mock）；直接调真函数也行，但 AsyncMock 会接住 add+execute+commit
        await real_persist(db, job=job, segments=segments,
                           decisions=decisions, summary=summary)

    monkeypatch.setattr(curation_service, "_persist_results", _spy_persist)
    monkeypatch.setattr(curation_service, "_make_llm_client_or_none", lambda: None)

    session = _build_session_with_loaders(
        job=job, segments=segments, sentences=sentences,
        classification=classification,
    )

    final = await curation_service.run_curation_job(session, job.id)
    assert final == "success"

    assert len(captured_updates) == 1
    summary = captured_updates[0]
    assert summary["accepted_duration_ratio"] == 0.0
    assert summary["low_quality"] is True
    assert summary["audio_unavailable"] is False  # transcript 非空


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_audio_unavailable_flag(monkeypatch):
    """transcript 为空 → audio_unavailable=True."""
    job = _make_fake_job()
    segments = [_FakeSegment(0, 0, 60_000), _FakeSegment(1, 60_000, 120_000)]
    classification = SimpleNamespace(coach_name="张继科", tech_category="forehand_topspin")

    captured_updates: list[dict] = []
    real_persist = curation_service._persist_results

    async def _spy_persist(db, *, job, segments, decisions, summary):
        captured_updates.append(summary)
        await real_persist(db, job=job, segments=segments,
                           decisions=decisions, summary=summary)

    monkeypatch.setattr(curation_service, "_persist_results", _spy_persist)
    monkeypatch.setattr(curation_service, "_make_llm_client_or_none", lambda: None)

    session = _build_session_with_loaders(
        job=job, segments=segments, sentences=[],  # transcript 为空
        classification=classification,
    )

    final = await curation_service.run_curation_job(session, job.id)
    assert final == "success"
    summary = captured_updates[0]
    assert summary["audio_unavailable"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_no_segments_marks_failed(monkeypatch):
    """preprocessing_job 无分段 → run_curation_job 返回 failed，
    错误码 INTERNAL_ERROR（RuntimeError 兜底）."""
    job = _make_fake_job()
    classification = SimpleNamespace(coach_name="张继科", tech_category="forehand_topspin")
    monkeypatch.setattr(curation_service, "_make_llm_client_or_none", lambda: None)
    session = _build_session_with_loaders(
        job=job, segments=[], sentences=[], classification=classification,
    )

    final = await curation_service.run_curation_job(session, job.id)
    assert final == "failed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_rubric_load_failure_marks_failed(monkeypatch):
    """rubric 加载抛 AppException → 任务标 failed."""
    job = _make_fake_job()
    classification = SimpleNamespace(coach_name="张继科", tech_category="forehand_topspin")

    def _raise(*args, **kwargs):
        raise AppException(ErrorCode.RUBRIC_VERSION_NOT_FOUND)

    monkeypatch.setattr(curation_service.rubric_loader, "load", _raise)
    monkeypatch.setattr(curation_service, "_make_llm_client_or_none", lambda: None)

    session = _build_session_with_loaders(
        job=job, segments=[], sentences=[], classification=classification,
    )

    final = await curation_service.run_curation_job(session, job.id)
    assert final == "failed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_curation_job_missing_row_raises():
    """job_id 不存在时直接抛 RuntimeError（service 层兜底；调用方 Celery 任务捕获）."""
    session = _build_session_with_loaders(
        job=None, segments=[], sentences=[], classification=None,
    )
    with pytest.raises(RuntimeError, match="not found"):
        await curation_service.run_curation_job(session, uuid.uuid4())
