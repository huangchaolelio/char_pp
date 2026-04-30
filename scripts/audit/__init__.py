"""Feature-018 漂移扫描与合规检查工具包。

职责：
- workflow_drift.py: 扫描代码侧与 docs/business-workflow.md 的章程级约束漂移
- spec_compliance.py: 扫描 specs/*/spec.md 是否包含「业务阶段映射」必填段
- _spec_sections.py: 六项子标签字面量的单一事实来源（Clarification Q8）

详见 specs/018-workflow-standardization/。
"""
