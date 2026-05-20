"""Feature-021 T051 — KB 抽取下游分段过滤关键护栏（spec SC-008）.

**这是 spec 中最重要的一项护栏**：保证被清洗判为 ``rejected`` /
``uncertain`` 的分段从未进入 KB 抽取的 LLM Prompt 拼装、姿态聚合或
任何下游计算。

具体实现：``download_video.execute()`` 在加载 ``preprocessing.segments``
之后立即按 ``video_curation_segment_results.effective_decision='accepted'``
过滤 segment_index 集合；只有过滤后的 segments 进入 head_object → 下载 →
``segments_processed`` 计数。

本测试用 mock 的 DB session + ``_load_preprocessing_view`` + 文件 I/O 桩，
验证：

1. accepted 集合传给后续 head_object / 下载循环
2. rejected / uncertain 的 ``cos_object_key`` 从未被 head_object 调用
3. ``output_summary.segments_processed = len(accepted)``
4. ``output_summary.segments_skipped_by_curation = len(rejected) + len(uncertain)``
5. ``output_summary.curation_job_id`` / ``curation_rubric_version`` 留痕
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
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


def _make_view(cos_key: str, n_segments: int = 5):
    pp_job_id = uuid4()
    segs = [
        _FakeSeg(
            segment_index=i,
            cos_object_key=f"{cos_key}/preprocessed/seg_{i:04d}.mp4",
            size_bytes=1000 + i,
        )
        for i in range(n_segments)
    ]
    return _FakeView(
        job_id=pp_job_id,
        cos_object_key=cos_key,
        has_audio=False,
        segments=segs,
    )


def _make_session_for_curation(
    *,
    accepted_indices: list[int] | None,
    gate_decision: str,
    accepted_duration_ratio: float,
):
    """Mock AsyncSession that returns:
       - 1st execute(): GateResult lookup (handled by patching evaluate_curation_gate)
       - 2nd execute(): SELECT segment_index WHERE effective_decision='accepted' rows
    Since we patch ``evaluate_curation_gate``, only the second query runs against the
    session — return the ``[(idx,), (idx,), ...]`` rows."""
    session = MagicMock()
    rows_result = MagicMock()
    rows_result.all = MagicMock(
        return_value=[(i,) for i in (accepted_indices or [])]
    )
    session.execute = AsyncMock(return_value=rows_result)
    return session


@pytest.mark.integration
@pytest.mark.asyncio
async def test_only_accepted_segments_reach_head_object_and_download(
    monkeypatch, tmp_path,
):
    """SC-008 关键护栏：被 rejected / uncertain 的分段在 head_object /
    下载循环中**绝不出现**。"""
    cos_key = "charhuang/x/y.mp4"
    view = _make_view(cos_key, n_segments=5)
    # 假定 accepted: indices 0, 2, 4；rejected/uncertain: 1, 3
    accepted_idx = [0, 2, 4]
    rejected_seg_keys = {
        view.segments[1].cos_object_key,
        view.segments[3].cos_object_key,
    }

    cur_jid = uuid4()
    from src.services.curation.kb_gate import GateResult

    head_object_calls: list[str] = []
    download_calls: list[str] = []

    def _fake_head(key):
        head_object_calls.append(key)
        return True

    def _fake_download(key, dest):
        download_calls.append(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 1000)
        return 1000

    # 依赖隔离
    monkeypatch.setattr(download_video, "_load_preprocessing_view",
                        AsyncMock(return_value=view))
    monkeypatch.setattr(download_video, "_cos_object_exists", _fake_head)
    monkeypatch.setattr(download_video, "_download_cos_to_file", _fake_download)
    monkeypatch.setattr(download_video, "_preprocessing_local_path",
                        lambda *a, **kw: tmp_path / "nonexistent")

    monkeypatch.setattr(
        "src.services.curation.kb_gate.evaluate_curation_gate",
        AsyncMock(return_value=GateResult(
            decision="ok",
            curation_job_id=cur_jid,
            curation_rubric_version="v1",
            accepted_duration_ratio=0.6,
        )),
    )

    # extraction_artifact_root 指向 tmp_path
    settings = MagicMock()
    settings.extraction_artifact_root = str(tmp_path / "kb_jobs")
    monkeypatch.setattr(download_video, "get_settings",
                        lambda: settings)

    session = _make_session_for_curation(
        accepted_indices=accepted_idx,
        gate_decision="ok",
        accepted_duration_ratio=0.6,
    )

    job_id = uuid4()
    fake_job = SimpleNamespace(id=job_id, cos_object_key=cos_key)
    fake_step = SimpleNamespace()

    result = await download_video.execute(session, fake_job, fake_step)

    # ── 关键护栏：rejected 分段 cos_object_key 从未出现在 head_object 调用中 ──
    for rejected_key in rejected_seg_keys:
        assert rejected_key not in head_object_calls, (
            f"FATAL SC-008 violation: rejected cos_key {rejected_key!r} "
            f"reached head_object()"
        )
        assert rejected_key not in download_calls, (
            f"FATAL SC-008 violation: rejected cos_key {rejected_key!r} "
            f"reached download()"
        )

    # ── 计数对账 ────────────────────────────────────────────────
    assert len(head_object_calls) == len(accepted_idx)
    summary = result["output_summary"]
    assert summary["segments_total"] == 5
    assert summary["segments_processed"] == 3
    assert summary["segments_skipped_by_curation"] == 2
    assert summary["segments_downloaded"] == 3
    assert summary["curation_job_id"] == str(cur_jid)
    assert summary["curation_rubric_version"] == "v1"
    assert summary["curation_warning"] is None
    assert summary["curation_bypass"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_curation_warning_marked_when_low_quality_warn(
    monkeypatch, tmp_path,
):
    """0 < accepted_duration_ratio < 0.3 ⇒ curation_warning='low_quality'."""
    cos_key = "charhuang/low_quality.mp4"
    view = _make_view(cos_key, n_segments=5)
    cur_jid = uuid4()

    from src.services.curation.kb_gate import GateResult

    monkeypatch.setattr(download_video, "_load_preprocessing_view",
                        AsyncMock(return_value=view))
    monkeypatch.setattr(download_video, "_cos_object_exists", lambda k: True)
    monkeypatch.setattr(download_video, "_download_cos_to_file",
                        lambda k, d: (d.parent.mkdir(parents=True, exist_ok=True),
                                      d.write_bytes(b"x" * 1000),
                                      1000)[-1])
    monkeypatch.setattr(download_video, "_preprocessing_local_path",
                        lambda *a, **kw: tmp_path / "nonexistent")

    monkeypatch.setattr(
        "src.services.curation.kb_gate.evaluate_curation_gate",
        AsyncMock(return_value=GateResult(
            decision="low_quality_warn",
            curation_job_id=cur_jid,
            curation_rubric_version="v1",
            accepted_duration_ratio=0.2,
        )),
    )

    settings = MagicMock()
    settings.extraction_artifact_root = str(tmp_path / "kb_jobs")
    monkeypatch.setattr(download_video, "get_settings", lambda: settings)

    session = _make_session_for_curation(
        accepted_indices=[0],
        gate_decision="low_quality_warn",
        accepted_duration_ratio=0.2,
    )
    job_id = uuid4()
    fake_job = SimpleNamespace(id=job_id, cos_object_key=cos_key)
    fake_step = SimpleNamespace()

    result = await download_video.execute(session, fake_job, fake_step)
    assert result["output_summary"]["curation_warning"] == "low_quality"
    assert result["output_summary"]["curation_bypass"] is False
    assert result["output_summary"]["segments_processed"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bypass_falls_back_to_full_segments(monkeypatch, tmp_path):
    """bypass=True ⇒ 跳过过滤、读全量 segments、curation_bypass=true 留痕."""
    cos_key = "charhuang/bypass_test.mp4"
    view = _make_view(cos_key, n_segments=4)

    from src.services.curation.kb_gate import GateResult

    monkeypatch.setattr(download_video, "_load_preprocessing_view",
                        AsyncMock(return_value=view))
    monkeypatch.setattr(download_video, "_cos_object_exists", lambda k: True)
    monkeypatch.setattr(download_video, "_download_cos_to_file",
                        lambda k, d: (d.parent.mkdir(parents=True, exist_ok=True),
                                      d.write_bytes(b"x" * 1000),
                                      1000)[-1])
    monkeypatch.setattr(download_video, "_preprocessing_local_path",
                        lambda *a, **kw: tmp_path / "nonexistent")
    monkeypatch.setattr(
        "src.services.curation.kb_gate.evaluate_curation_gate",
        AsyncMock(return_value=GateResult(decision="bypassed")),
    )
    settings = MagicMock()
    settings.extraction_artifact_root = str(tmp_path / "kb_jobs")
    monkeypatch.setattr(download_video, "get_settings", lambda: settings)

    session = MagicMock()
    session.execute = AsyncMock()  # 不应被调用（bypass 不查 DB）

    job_id = uuid4()
    fake_job = SimpleNamespace(id=job_id, cos_object_key=cos_key)
    fake_step = SimpleNamespace()

    result = await download_video.execute(session, fake_job, fake_step)
    summary = result["output_summary"]
    assert summary["segments_total"] == 4
    assert summary["segments_processed"] == 4  # 全量直通
    assert summary["segments_skipped_by_curation"] == 0
    assert summary["curation_bypass"] is True
    assert summary["curation_job_id"] is None
