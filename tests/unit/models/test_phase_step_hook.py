"""Feature-018 T014 — ORM ``before_insert`` hook 单元测试.

覆盖 7 个场景（tasks.md T014）：
1. analysis_tasks + video_classification + parent_scan_task_id IS NULL ⇒ TRAINING/scan_cos_videos
2. analysis_tasks + video_classification + parent_scan_task_id NOT NULL ⇒ TRAINING/classify_video
3. analysis_tasks + video_preprocessing ⇒ TRAINING/preprocess_video
4. analysis_tasks + kb_extraction ⇒ TRAINING/extract_kb
5. analysis_tasks + athlete_diagnosis ⇒ INFERENCE/diagnose_athlete
6. 显式传入 phase+step 被尊重（不覆盖）
7. 只传 phase 未传 step ⇒ raises ValueError("PHASE_STEP_UNMAPPED...")

由于实际的 before_insert 钩子在 SQLAlchemy flush 时触发，这里直接调用
内部函数 ``_assign_phase_step`` 模拟 flush 前的派生动作，无需真实 DB 连接。
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from src.models._phase_step_hook import (
    _assign_phase_step,
    _derive_for_analysis_task,
)
from src.models.analysis_task import AnalysisTask, BusinessPhase, TaskType
from src.models.extraction_job import ExtractionJob
from src.models.tech_knowledge_base import TechKnowledgeBase
from src.models.video_preprocessing_job import VideoPreprocessingJob


def _make_analysis_task(
    task_type: TaskType,
    *,
    parent_scan_task_id: uuid.UUID | None = None,
) -> AnalysisTask:
    """创建一个未绑定到 session 的 AnalysisTask 实例用于派生测试."""
    t = AnalysisTask()
    t.task_type = task_type
    t.parent_scan_task_id = parent_scan_task_id
    t.business_phase = None  # type: ignore[assignment]
    t.business_step = None  # type: ignore[assignment]
    return t


# ── 场景 1 ─────────────────────────────────────────────────────────────
def test_analysis_task_scan_cos_videos_when_parent_null():
    row = _make_analysis_task(TaskType.video_classification, parent_scan_task_id=None)
    phase, step = _derive_for_analysis_task(row)
    assert phase == BusinessPhase.TRAINING.value
    assert step == "scan_cos_videos"


# ── 场景 2 ─────────────────────────────────────────────────────────────
def test_analysis_task_classify_video_when_parent_not_null():
    row = _make_analysis_task(
        TaskType.video_classification, parent_scan_task_id=uuid.uuid4()
    )
    phase, step = _derive_for_analysis_task(row)
    assert phase == BusinessPhase.TRAINING.value
    assert step == "classify_video"


# ── 场景 3 / 4 / 5 ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "task_type, expected_phase, expected_step",
    [
        (TaskType.video_preprocessing, BusinessPhase.TRAINING.value, "preprocess_video"),
        (TaskType.kb_extraction, BusinessPhase.TRAINING.value, "extract_kb"),
        (TaskType.athlete_diagnosis, BusinessPhase.INFERENCE.value, "diagnose_athlete"),
    ],
)
def test_analysis_task_other_task_types(task_type, expected_phase, expected_step):
    row = _make_analysis_task(task_type)
    phase, step = _derive_for_analysis_task(row)
    assert phase == expected_phase
    assert step == expected_step


# ── 场景 6：显式传入被尊重 ───────────────────────────────────────────
def test_explicit_phase_step_respected():
    """调用方同时显式设置 phase + step ⇒ 钩子尊重，不覆盖."""
    row = _make_analysis_task(TaskType.kb_extraction)
    row.business_phase = BusinessPhase.STANDARDIZATION.value  # 故意跨阶段
    row.business_step = "custom_step"
    _assign_phase_step(None, None, row)
    assert row.business_phase == BusinessPhase.STANDARDIZATION.value
    assert row.business_step == "custom_step"


# ── 场景 7：只传 phase 或只传 step ⇒ ValueError ──────────────────────
def test_only_phase_set_raises():
    row = _make_analysis_task(TaskType.kb_extraction)
    row.business_phase = BusinessPhase.TRAINING.value
    row.business_step = None  # type: ignore[assignment]
    with pytest.raises(ValueError, match="PHASE_STEP_UNMAPPED"):
        _assign_phase_step(None, None, row)


def test_only_step_set_raises():
    row = _make_analysis_task(TaskType.kb_extraction)
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = "extract_kb"
    with pytest.raises(ValueError, match="PHASE_STEP_UNMAPPED"):
        _assign_phase_step(None, None, row)


# ── 派生规则：ExtractionJob 固定值 ────────────────────────────────────
def test_extraction_job_fixed_defaults():
    row = ExtractionJob()
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = None  # type: ignore[assignment]
    _assign_phase_step(None, None, row)
    assert row.business_phase == BusinessPhase.TRAINING.value
    assert row.business_step == "extract_kb"


def test_video_preprocessing_job_fixed_defaults():
    row = VideoPreprocessingJob()
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = None  # type: ignore[assignment]
    _assign_phase_step(None, None, row)
    assert row.business_phase == BusinessPhase.TRAINING.value
    assert row.business_step == "preprocess_video"


def test_tech_knowledge_base_fixed_defaults():
    row = TechKnowledgeBase()
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = None  # type: ignore[assignment]
    _assign_phase_step(None, None, row)
    assert row.business_phase == BusinessPhase.STANDARDIZATION.value
    assert row.business_step == "kb_version_activate"


# ── 未知 task_type ⇒ PHASE_STEP_UNMAPPED ──────────────────────────────
def test_unknown_task_type_raises():
    row = _make_analysis_task(TaskType.kb_extraction)
    # 使用 Mock 绕过 enum 类型限制，模拟未知 task_type 值
    row.task_type = MagicMock()
    row.task_type.value = "unknown_x"
    with pytest.raises(ValueError, match="PHASE_STEP_UNMAPPED"):
        _derive_for_analysis_task(row)
