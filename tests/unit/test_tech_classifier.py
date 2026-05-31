"""Feature-023 物理删除：tests/unit/test_tech_classifier.py 已废弃.

旧测试依赖 `TECH_CATEGORIES` / `get_tech_label()` 等已物理删除的符号；
其语义已被 `tests/unit/services/test_tech_classifier_v2.py` 完全替代。

按章程 v2.0.0 原则 IV/IX：接口下线 = 物理删除。本文件保留为占位以避免
旧 import 路径被误认为还存在，未来可由维护者完全删除。
"""

import pytest

pytest.skip(
    "Feature-023 物理删除：旧 21 类 TECH_CATEGORIES 测试已废弃；"
    "见 tests/unit/services/test_tech_classifier_v2.py",
    allow_module_level=True,
)
