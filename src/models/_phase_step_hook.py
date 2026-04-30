"""Feature-018 — ORM ``before_insert`` hook 自动派生 ``business_phase`` / ``business_step``。

权威参考: specs/018-workflow-standardization/data-model.md § 3 + § 4。

设计约束（章程原则 X / Q4 决议）：
- 四张业务表 MUST NOT 在业务代码中手动填充 phase / step，MUST 由本钩子统一派生
- 钩子为"单一事实来源"：表名 → 默认值（或派生函数）
- Fail-Fast：未知 task_type、或仅传入 phase/step 之一 ⇒ ``ValueError(PHASE_STEP_UNMAPPED)``
- 显式传入（两列同时设置）视为"授权覆写"，钩子 MUST 尊重

调用点: ``src/db/session.py`` 模块顶部在 ``Base = declarative_base()`` 之后
导入并调用 ``register_phase_step_hooks()``；保证 API / Celery / Alembic 三种
加载入口都会触发一次。
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy import event, inspect as sa_inspect

from src.models.analysis_task import AnalysisTask, BusinessPhase, TaskType
from src.models.extraction_job import ExtractionJob
from src.models.tech_knowledge_base import TechKnowledgeBase
from src.models.video_preprocessing_job import VideoPreprocessingJob


def _derive_for_analysis_task(row: AnalysisTask) -> tuple[str, str]:
    """派生 analysis_tasks 行的 (phase, step)。

    规则见 data-model.md § 3.1：
    - video_classification + parent_scan_task_id IS NULL ⇒ (TRAINING, scan_cos_videos)
    - video_classification + parent_scan_task_id NOT NULL ⇒ (TRAINING, classify_video)
    - video_preprocessing ⇒ (TRAINING, preprocess_video)
    - kb_extraction ⇒ (TRAINING, extract_kb)
    - athlete_diagnosis ⇒ (INFERENCE, diagnose_athlete)
    """
    tt = row.task_type
    if tt == TaskType.video_classification:
        step = "scan_cos_videos" if row.parent_scan_task_id is None else "classify_video"
        return (BusinessPhase.TRAINING.value, step)
    if tt == TaskType.video_preprocessing:
        return (BusinessPhase.TRAINING.value, "preprocess_video")
    if tt == TaskType.kb_extraction:
        return (BusinessPhase.TRAINING.value, "extract_kb")
    if tt == TaskType.athlete_diagnosis:
        return (BusinessPhase.INFERENCE.value, "diagnose_athlete")
    raise ValueError(f"PHASE_STEP_UNMAPPED: unknown task_type={tt!r}")


# 表名 → (默认 phase, 默认 step) 或派生函数
_TABLE_DEFAULTS: dict[type, tuple[str, str] | Callable[[object], tuple[str, str]]] = {
    ExtractionJob: (BusinessPhase.TRAINING.value, "extract_kb"),
    VideoPreprocessingJob: (BusinessPhase.TRAINING.value, "preprocess_video"),
    TechKnowledgeBase: (BusinessPhase.STANDARDIZATION.value, "kb_version_activate"),
    AnalysisTask: _derive_for_analysis_task,
}


def _assign_phase_step(mapper, connection, target) -> None:  # pragma: no cover - bound at import
    """before_insert 钩子：若调用方未显式传值，则按派生规则自动填充。

    Fail-Fast 场景：
    - 只设置 phase 或只设置 step（非同时）⇒ PHASE_STEP_UNMAPPED
    - 未知表类型 ⇒ PHASE_STEP_UNMAPPED
    - 派生函数抛 PHASE_STEP_UNMAPPED（如未知 task_type）
    """
    state = sa_inspect(target)

    phase_set = target.business_phase is not None
    step_set = target.business_step is not None

    if phase_set and step_set:
        # 授权覆写：调用方显式传值，钩子尊重
        return

    if phase_set ^ step_set:
        raise ValueError(
            "PHASE_STEP_UNMAPPED: must set both business_phase and business_step, or neither"
        )

    rule = _TABLE_DEFAULTS.get(type(target))
    if rule is None:
        raise ValueError(
            f"PHASE_STEP_UNMAPPED: no default for table={type(target).__name__}"
        )

    phase, step = rule(target) if callable(rule) else rule
    target.business_phase = phase
    target.business_step = step


_hooks_registered = False


def register_phase_step_hooks() -> None:
    """幂等注册钩子；可被多个入口重复调用而只真正注册一次。"""
    global _hooks_registered
    if _hooks_registered:
        return
    for model_cls in _TABLE_DEFAULTS:
        event.listen(model_cls, "before_insert", _assign_phase_step)
    _hooks_registered = True
