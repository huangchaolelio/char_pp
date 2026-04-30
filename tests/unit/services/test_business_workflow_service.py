"""Feature-018 — BusinessWorkflowService 校验矩阵单元测试.

覆盖：
- 合法 (phase, step, task_type) 三元组 ⇒ 不抛异常
- phase + task_type 矛盾 ⇒ INVALID_PHASE_STEP_COMBO
- phase + step + task_type 矛盾 ⇒ INVALID_PHASE_STEP_COMBO
- task_type 为空 ⇒ 通过（不做校验）
- phase / step 单独指定 + 无 task_type ⇒ 通过

聚合查询逻辑另由 integration test 覆盖（需真实 DB）。
"""

from __future__ import annotations

import pytest

from src.api.errors import AppException, ErrorCode
from src.services.business_workflow_service import (
    _validate_phase_step_task_type_combo,
)


class TestPhaseStepTaskTypeMatrix:
    # ── 合法组合 ─────────────────────────────────────
    def test_valid_training_extract_kb(self):
        _validate_phase_step_task_type_combo("TRAINING", "extract_kb", "kb_extraction")
        # no exception

    def test_valid_inference_diagnose(self):
        _validate_phase_step_task_type_combo("INFERENCE", "diagnose_athlete", "athlete_diagnosis")

    def test_valid_training_classify_video(self):
        _validate_phase_step_task_type_combo("TRAINING", "classify_video", "video_classification")

    def test_valid_training_scan_cos(self):
        _validate_phase_step_task_type_combo("TRAINING", "scan_cos_videos", "video_classification")

    def test_valid_training_preprocess(self):
        _validate_phase_step_task_type_combo("TRAINING", "preprocess_video", "video_preprocessing")

    # ── task_type 空 ⇒ 通过 ──────────────────────────
    def test_none_task_type_bypasses(self):
        _validate_phase_step_task_type_combo("INFERENCE", "diagnose_athlete", None)
        _validate_phase_step_task_type_combo("TRAINING", None, None)

    # ── 仅 phase 指定 + task_type 矛盾 ───────────────
    def test_phase_inference_vs_kb_extraction(self):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo("INFERENCE", None, "kb_extraction")
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO

    def test_phase_training_vs_athlete_diagnosis(self):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo("TRAINING", None, "athlete_diagnosis")
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO

    # ── phase + step + task_type 三者矛盾 ───────────
    def test_phase_step_task_type_all_three_conflict(self):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo(
                "TRAINING", "extract_kb", "athlete_diagnosis"
            )
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO

    # ── STANDARDIZATION 阶段不允许 task_type 指定 ──
    def test_standardization_with_any_task_type_conflicts(self):
        with pytest.raises(AppException) as exc_info:
            _validate_phase_step_task_type_combo(
                "STANDARDIZATION", "review_conflicts", "kb_extraction"
            )
        assert exc_info.value.code == ErrorCode.INVALID_PHASE_STEP_COMBO
