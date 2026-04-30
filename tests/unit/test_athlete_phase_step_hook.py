"""Feature-020 T011 — 运动员侧 2 个新 TaskType 的派生钩子单测.

验证 ``_phase_step_hook._derive_for_analysis_task`` 对下列 2 种 TaskType 的
正确派生：

- ``athlete_video_classification`` ⇒ ``(INFERENCE, scan_athlete_videos)``
- ``athlete_video_preprocessing``  ⇒ ``(INFERENCE, preprocess_athlete_video)``

并覆盖 Fail-Fast：未知 task_type 仍抛 ``ValueError("PHASE_STEP_UNMAPPED...")``。
"""

from __future__ import annotations

import pytest

from src.models._phase_step_hook import _assign_phase_step, _derive_for_analysis_task
from src.models.analysis_task import AnalysisTask, BusinessPhase, TaskType


def _make_row(task_type: TaskType) -> AnalysisTask:
    row = AnalysisTask()
    row.task_type = task_type
    row.parent_scan_task_id = None
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = None  # type: ignore[assignment]
    return row


@pytest.mark.parametrize(
    "task_type, expected_step",
    [
        (TaskType.athlete_video_classification, "scan_athlete_videos"),
        (TaskType.athlete_video_preprocessing, "preprocess_athlete_video"),
    ],
)
def test_athlete_task_types_derive_to_inference(
    task_type: TaskType, expected_step: str
) -> None:
    """Feature-020 两个新 task_type 都派生到 INFERENCE 阶段."""
    row = _make_row(task_type)
    phase, step = _derive_for_analysis_task(row)
    assert phase == BusinessPhase.INFERENCE.value
    assert step == expected_step


@pytest.mark.parametrize(
    "task_type, expected_step",
    [
        (TaskType.athlete_video_classification, "scan_athlete_videos"),
        (TaskType.athlete_video_preprocessing, "preprocess_athlete_video"),
    ],
)
def test_assign_phase_step_hook_fills_athlete_fields(
    task_type: TaskType, expected_step: str
) -> None:
    """before_insert 钩子作用于运动员侧 task_type 时自动填 phase/step."""
    row = _make_row(task_type)
    _assign_phase_step(mapper=None, connection=None, target=row)
    assert row.business_phase == BusinessPhase.INFERENCE.value
    assert row.business_step == expected_step


def test_unknown_task_type_still_raises() -> None:
    """未知 task_type（模拟）仍抛 PHASE_STEP_UNMAPPED，防止钩子静默放过."""
    row = AnalysisTask()
    # 强制注入一个非枚举值（在工程上该情况不会发生，仅测试 fail-safe 分支）
    row.task_type = "__nonexistent__"  # type: ignore[assignment]
    row.parent_scan_task_id = None
    row.business_phase = None  # type: ignore[assignment]
    row.business_step = None  # type: ignore[assignment]
    with pytest.raises(ValueError, match="PHASE_STEP_UNMAPPED"):
        _derive_for_analysis_task(row)
