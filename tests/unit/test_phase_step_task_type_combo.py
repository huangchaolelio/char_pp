"""Feature-020 · T049 · (phase, step, task_type) 组合校验单测.

覆盖 `_validate_phase_step_task_type_combo` 对 Feature-020 新增的 3 个组合：
  - (INFERENCE, scan_athlete_videos, athlete_video_classification)   ✓
  - (INFERENCE, preprocess_athlete_video, athlete_video_preprocessing) ✓
  - (INFERENCE, diagnose_athlete, athlete_diagnosis)                 ✓

以及 TRAINING 阶段不允许运动员相关步骤的反例:
  - (TRAINING, scan_athlete_videos, *)                               ✗
  - (INFERENCE, scan_athlete_videos, kb_extraction)                  ✗
  - (INFERENCE, preprocess_athlete_video, video_preprocessing)       ✗
"""

from __future__ import annotations

import pytest

from src.api.errors import AppException, ErrorCode
from src.services.business_workflow_service import (
    _validate_phase_step_task_type_combo,
)


class TestPhaseStepTaskTypeCombo:

    # ── 合法组合：不抛异常 ────────────────────────────────────────────
    @pytest.mark.parametrize(
        "phase, step, task_type",
        [
            ("INFERENCE", "scan_athlete_videos", "athlete_video_classification"),
            ("INFERENCE", "preprocess_athlete_video", "athlete_video_preprocessing"),
            ("INFERENCE", "diagnose_athlete", "athlete_diagnosis"),
            # 兼容性：既有 Feature-013 / F-001 组合仍合法
            ("INFERENCE", "diagnose_athlete", "athlete_diagnosis"),
        ],
    )
    def test_valid_combo_does_not_raise(self, phase, step, task_type):
        # 不抛任何异常视为通过
        _validate_phase_step_task_type_combo(phase, step, task_type)

    # ── 非法组合：必须抛 INVALID_PHASE_STEP_COMBO ─────────────────────
    @pytest.mark.parametrize(
        "phase, step, task_type, conflict_kind",
        [
            # TRAINING 阶段不允许运动员扫描
            ("TRAINING", "scan_athlete_videos", "athlete_video_classification",
             "phase_step_task_type_mismatch"),
            # INFERENCE + scan_athlete_videos 只允许 athlete_video_classification
            ("INFERENCE", "scan_athlete_videos", "kb_extraction",
             "phase_step_task_type_mismatch"),
            # INFERENCE + preprocess_athlete_video 只允许 athlete_video_preprocessing
            ("INFERENCE", "preprocess_athlete_video", "video_preprocessing",
             "phase_step_task_type_mismatch"),
            # INFERENCE + diagnose_athlete 只允许 athlete_diagnosis
            ("INFERENCE", "diagnose_athlete", "video_classification",
             "phase_step_task_type_mismatch"),
        ],
    )
    def test_invalid_combo_raises(self, phase, step, task_type, conflict_kind):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo(phase, step, task_type)
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO
        assert exc_info.value.details["phase"] == phase
        assert exc_info.value.details["step"] == step
        assert exc_info.value.details["task_type"] == task_type
        assert exc_info.value.details["conflict"] == conflict_kind

    # ── phase 仅与 task_type 指定（不指定 step）的场景 ────────────────
    def test_phase_alone_with_inference_allows_athlete_types(self):
        _validate_phase_step_task_type_combo("INFERENCE", None, "athlete_video_classification")
        _validate_phase_step_task_type_combo("INFERENCE", None, "athlete_video_preprocessing")
        _validate_phase_step_task_type_combo("INFERENCE", None, "athlete_diagnosis")

    def test_phase_alone_training_rejects_athlete_classification(self):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo(
                "TRAINING", None, "athlete_video_classification"
            )
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO
        assert exc_info.value.details["conflict"] == "phase_task_type_mismatch"

    # ── 边界：task_type=None 或 (phase,step) 均 None 时无校验 ─────────
    def test_no_task_type_is_noop(self):
        _validate_phase_step_task_type_combo("INFERENCE", "scan_athlete_videos", None)
        _validate_phase_step_task_type_combo(None, None, None)
        _validate_phase_step_task_type_combo(None, None, "athlete_diagnosis")
