"""Feature-021 T052 — accepted_duration_ratio==0 ⇒ LOW_QUALITY_SKIP 短路.

验证 spec FR-009 业务结果型短路：当清洗后视频 accepted_duration_ratio==0，
KB 抽取的 ``download_video`` step 抛 ``RuntimeError("LOW_QUALITY_SKIP: ...")``。
此 RuntimeError 由 orchestrator 转写为 ``extraction_jobs.status='failed'`` +
``error_code='LOW_QUALITY_SKIP'``，关键字段 ``segments_processed`` 为 0、
``kb_items_count``（由后续 step 派生）也为 0；不调 LLM、不耗 token。

特别强调：
- 错误码必须以 ``LOW_QUALITY_SKIP`` 前缀开头（运维可 grep / 任务监控可识别）
- error_message 包含 ``curation_job_id`` 反查锚点
- 任何下游 step（pose / audio / kb_extract）都不应被触发
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.kb_extraction_pipeline.step_executors import download_video


@dataclass
class _FakeView:
    job_id: object
    cos_object_key: str
    has_audio: bool = False
    audio: dict | None = None
    segments: list = field(default_factory=list)


@dataclass
class _FakeSeg:
    segment_index: int
    cos_object_key: str
    size_bytes: int = 1000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_low_quality_skip_raises_with_prefix(monkeypatch, tmp_path):
    """accepted_duration_ratio==0 ⇒ download_video 抛 RuntimeError；
    错误码前缀必须是 LOW_QUALITY_SKIP，error_message 含 curation_job_id."""
    cos_key = "charhuang/all_rejected.mp4"
    cur_jid = uuid4()
    view = _FakeView(
        job_id=uuid4(),
        cos_object_key=cos_key,
        segments=[
            _FakeSeg(segment_index=i, cos_object_key=f"x/seg_{i:04d}.mp4")
            for i in range(3)
        ],
    )

    from src.services.curation.kb_gate import GateResult

    head_object_calls: list[str] = []
    download_calls: list[str] = []

    monkeypatch.setattr(download_video, "_load_preprocessing_view",
                        AsyncMock(return_value=view))
    monkeypatch.setattr(download_video, "_cos_object_exists",
                        lambda k: head_object_calls.append(k) or True)
    monkeypatch.setattr(download_video, "_download_cos_to_file",
                        lambda k, d: download_calls.append(k))
    monkeypatch.setattr(
        "src.services.curation.kb_gate.evaluate_curation_gate",
        AsyncMock(return_value=GateResult(
            decision="low_quality_skip",
            curation_job_id=cur_jid,
            curation_rubric_version="v1",
            accepted_duration_ratio=0.0,
        )),
    )

    settings = MagicMock()
    settings.extraction_artifact_root = str(tmp_path / "kb_jobs")
    monkeypatch.setattr(download_video, "get_settings", lambda: settings)

    # 即使 DB 端真的查 effective_decision='accepted'，结果也是空集合
    session = MagicMock()
    rows_result = MagicMock()
    rows_result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=rows_result)

    fake_job = SimpleNamespace(id=uuid4(), cos_object_key=cos_key)
    fake_step = SimpleNamespace()

    with pytest.raises(RuntimeError) as exc_info:
        await download_video.execute(session, fake_job, fake_step)

    msg = str(exc_info.value)
    assert msg.startswith("LOW_QUALITY_SKIP:"), (
        f"error message must be prefixed with 'LOW_QUALITY_SKIP:' for grep / "
        f"runbook lookup, got: {msg!r}"
    )
    assert str(cur_jid) in msg, "error message must include curation_job_id 反查锚点"
    assert cos_key in msg, "error message must include cos_object_key"

    # 关键护栏：低质量短路时不应触碰 head_object / download
    assert head_object_calls == [], (
        "FATAL: head_object should never be called on a low_quality_skip job"
    )
    assert download_calls == [], (
        "FATAL: download should never be called on a low_quality_skip job"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_low_quality_skip_distinguished_from_real_failure(
    monkeypatch, tmp_path,
):
    """LOW_QUALITY_SKIP 与真实 SEGMENT_MISSING 必须用前缀区分.

    business-workflow.md § 7.4 错误码表中两者前缀不同；运维 grep 时不应混淆。
    """
    from src.services.kb_extraction_pipeline.error_codes import SEGMENT_MISSING

    # 1) low_quality_skip 路径：错误码必须以 'LOW_QUALITY_SKIP' 前缀开头
    cos_key = "charhuang/lq.mp4"
    cur_jid = uuid4()
    view = _FakeView(
        job_id=uuid4(), cos_object_key=cos_key,
        segments=[_FakeSeg(0, "x/seg_0000.mp4")],
    )

    from src.services.curation.kb_gate import GateResult

    monkeypatch.setattr(download_video, "_load_preprocessing_view",
                        AsyncMock(return_value=view))
    monkeypatch.setattr(download_video, "_cos_object_exists", lambda k: True)
    monkeypatch.setattr(
        "src.services.curation.kb_gate.evaluate_curation_gate",
        AsyncMock(return_value=GateResult(
            decision="low_quality_skip",
            curation_job_id=cur_jid,
            curation_rubric_version="v1",
            accepted_duration_ratio=0.0,
        )),
    )
    settings = MagicMock()
    settings.extraction_artifact_root = str(tmp_path / "kb")
    monkeypatch.setattr(download_video, "get_settings", lambda: settings)
    session = MagicMock()
    rows_result = MagicMock()
    rows_result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=rows_result)

    with pytest.raises(RuntimeError) as exc_info:
        await download_video.execute(
            session, SimpleNamespace(id=uuid4(), cos_object_key=cos_key),
            SimpleNamespace(),
        )
    assert str(exc_info.value).startswith("LOW_QUALITY_SKIP:")
    assert SEGMENT_MISSING not in str(exc_info.value)
