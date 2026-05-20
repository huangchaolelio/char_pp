"""Feature-021 内容清洗子包私域错误常量.

设计原则：
- 服务层抛 ``AppException(ErrorCode.XXX)``；本模块只是把 ``src/api/errors.py``
  的全局枚举重新导出，便于 service 子包内 ``from .error_codes import ...``
  保持 import 路径短。
- **不在此处定义新枚举值**——所有错误码的单一事实来源仍是
  ``src/api/errors.py::ErrorCode``（章程原则 IX：错误码集中化 + CI 守卫）。
- 业务结果型常量（``LOW_QUALITY_SKIP`` / ``CURATION_LLM_UNAVAILABLE``）以裸字符串
  导出，专门用于写入 ``video_curation_segment_results.rejection_reason`` /
  ``extraction_jobs.error_code`` JSONB 字段，**不走异常路径**。
"""

from __future__ import annotations

from src.api.errors import ErrorCode

# ── 异常路径错误码（透传 src/api/errors.py 单一事实来源） ─────────────────
CURATION_REQUIRED = ErrorCode.CURATION_REQUIRED
RUBRIC_INVALID = ErrorCode.RUBRIC_INVALID
RUBRIC_VERSION_NOT_FOUND = ErrorCode.RUBRIC_VERSION_NOT_FOUND
CURATION_TIMEOUT = ErrorCode.CURATION_TIMEOUT
CURATION_RUBRIC_MISMATCH = ErrorCode.CURATION_RUBRIC_MISMATCH

# ── 业务结果型字符串（写到 DB 字段，不抛异常） ────────────────────────────
# 这两个值同时也在 ``ErrorCode`` 枚举中登记（HTTP 200 业务结果），但 service
# 层在 segment-level 决策时用裸字符串写 ``rejection_reason``，避免 import 循环。
LOW_QUALITY_SKIP = ErrorCode.LOW_QUALITY_SKIP.value      # "LOW_QUALITY_SKIP"
CURATION_LLM_UNAVAILABLE = ErrorCode.CURATION_LLM_UNAVAILABLE.value  # "CURATION_LLM_UNAVAILABLE"


__all__ = [
    "CURATION_REQUIRED",
    "RUBRIC_INVALID",
    "RUBRIC_VERSION_NOT_FOUND",
    "CURATION_TIMEOUT",
    "CURATION_RUBRIC_MISMATCH",
    "LOW_QUALITY_SKIP",
    "CURATION_LLM_UNAVAILABLE",
]
