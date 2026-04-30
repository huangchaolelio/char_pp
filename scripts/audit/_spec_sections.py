"""Feature-018「业务阶段映射」六项子标签字面量的单一事实来源。

Clarification Q8 决议：禁止在 `spec_compliance.py` / `workflow_drift.py` 内
硬编码这六个字符串；所有校验 MUST 通过导入此模块的常量。

常量需与下列文件同步：
- `.specify/templates/spec-template.md` § 业务阶段映射（模板六项子标签）
- `specs/018-workflow-standardization/spec.md` FR-010（CI 合规扫描需求）
- `docs/business-workflow.md` § 章程原则 X（业务流程对齐）

任一来源的字段集变更 MUST 同步本常量，否则 `workflow_drift.py`
会输出 `DRIFT: spec_template_fields ...` 阻断合并。
"""

from __future__ import annotations

# 业务阶段映射六项必填子标签（Clarification Q8 — 六项字面量的单一事实来源）
REQUIRED_BUSINESS_STAGE_FIELDS: tuple[str, ...] = (
    "所属阶段",
    "所属步骤",
    "DoD 引用",
    "可观测锚点",
    "章程级约束影响",
    "回滚剧本",
)

# 「业务阶段映射」小段的节标题（用于 spec_compliance.py 匹配）
BUSINESS_STAGE_SECTION_HEADER: str = "业务阶段映射"
