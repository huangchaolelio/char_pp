"""Feature-021 T019 — video_curation 派生钩子单测.

验证 ``_phase_step_hook._derive_for_analysis_task`` 对 ``video_curation`` 的
正确派生：``(TRAINING, curate_segments)``。同步覆盖 ``_assign_phase_step``
``before_insert`` 钩子链路。
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


def test_video_curation_derives_to_training_curate_segments() -> None:
    """video_curation 派生到 (TRAINING, curate_segments)."""
    row = _make_row(TaskType.video_curation)
    phase, step = _derive_for_analysis_task(row)
    assert phase == BusinessPhase.TRAINING.value
    assert step == "curate_segments"


def test_assign_phase_step_hook_fills_video_curation_fields() -> None:
    """before_insert 钩子作用于 video_curation 时自动填 phase/step."""
    row = _make_row(TaskType.video_curation)
    _assign_phase_step(mapper=None, connection=None, target=row)
    assert row.business_phase == BusinessPhase.TRAINING.value
    assert row.business_step == "curate_segments"


def test_video_curation_in_phase_step_matrix_and_phase_set() -> None:
    """业务流程矩阵 + phase 集合都需含 video_curation 才算完整登记."""
    from src.services.business_workflow_service import (
        _PHASE_STEP_TASK_TYPE_MATRIX,
        _PHASE_TASK_TYPES,
    )

    assert ("TRAINING", "curate_segments") in _PHASE_STEP_TASK_TYPE_MATRIX
    assert _PHASE_STEP_TASK_TYPE_MATRIX[("TRAINING", "curate_segments")] == {
        "video_curation"
    }
    assert "video_curation" in _PHASE_TASK_TYPES["TRAINING"]


def test_curate_segments_in_router_whitelist() -> None:
    """tasks.py::list_tasks 的 step 白名单需要含 curate_segments，
    否则任务监控按 business_step=curate_segments 筛选会被 422 拦下."""
    import inspect

    from src.api.routers import tasks as tasks_router_mod

    src = inspect.getsource(tasks_router_mod)
    # _VALID_BUSINESS_STEPS 是函数体内常量，直接源码扫描
    assert '"curate_segments"' in src or "'curate_segments'" in src, (
        "src/api/routers/tasks.py::_VALID_BUSINESS_STEPS 必须含 'curate_segments'"
    )


def test_video_curation_phase_step_combo_validates() -> None:
    """三元组合法校验：(TRAINING, curate_segments, video_curation) 通过；
    (INFERENCE, *, video_curation) 拒绝。"""
    from src.api.errors import AppException, ErrorCode
    from src.services.business_workflow_service import (
        _validate_phase_step_task_type_combo,
    )

    # 合法
    _validate_phase_step_task_type_combo(
        "TRAINING", "curate_segments", "video_curation"
    )
    # 非法：phase 错位
    with pytest.raises(AppException) as excinfo:
        _validate_phase_step_task_type_combo("INFERENCE", None, "video_curation")
    assert excinfo.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO
