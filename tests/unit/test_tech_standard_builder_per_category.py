"""Feature-023 物理删除遗留：本测试模块依赖已被物理删除/重命名的旧字段或 enum.

业务语义已被新测试覆盖：
  - tech_classifier: tests/unit/services/test_tech_classifier_v2.py
  - kb extraction gate: tests/integration/test_kb_extraction_action_gate.py
  - phase 7 contracts: tests/contract/test_phase7_action_aggregation.py
  - migration 0022: tests/integration/test_migration_0022_taxonomy.py

按章程 v2.0.0 原则 IV/IX：接口下线 = 物理删除。本文件保留 skip stub
以避免 collection 阶段 ImportError，待后续维护者按 Feature-023 ORM 重写。
"""

import pytest

pytest.skip(
    "Feature-023 物理删除遗留：依赖旧 tech_category / TECH_CATEGORIES / 旧迁移列；"
    "新测试已覆盖业务语义，本模块待 P3 重写。",
    allow_module_level=True,
)
