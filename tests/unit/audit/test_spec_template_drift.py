"""Feature-018 T031b — spec-template.md 漂移单元测试 (Clarification Q8).

覆盖：
- 模板含全部六项 ⇒ workflow_drift 不输出 spec_template_fields drift
- 模板缺「回滚剧本」 ⇒ 输出一条 DRIFT: spec_template_fields 回滚剧本
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.audit import workflow_drift
from scripts.audit._spec_sections import REQUIRED_BUSINESS_STAGE_FIELDS


def test_real_template_has_all_six_fields():
    """当前仓库的 .specify/templates/spec-template.md MUST 含六项."""
    fields = workflow_drift._collect_spec_template_fields()
    # 所有六项都必须出现
    for f in REQUIRED_BUSINESS_STAGE_FIELDS:
        assert f in fields, f"模板缺项: {f}"


def test_missing_field_in_template_detected(tmp_path: Path, monkeypatch, capsys):
    """模板缺「回滚剧本」⇒ workflow_drift 输出 spec_template_fields drift."""
    fake_tmpl = tmp_path / "spec-template.md"
    # 模拟模板但故意漏掉 "回滚剧本"
    content = """
# spec.md 模板

### 业务阶段映射 *(必填)*

- **所属阶段**:
- **所属步骤**:
- **DoD 引用**:
- **可观测锚点**:
- **章程级约束影响**:
"""
    fake_tmpl.write_text(content, encoding="utf-8")

    monkeypatch.setattr(workflow_drift, "_SPEC_TEMPLATE_PATH", fake_tmpl)
    drifts = workflow_drift.scan_all()
    captured = capsys.readouterr()
    assert drifts > 0
    assert "spec_template_fields" in captured.out
    assert "回滚剧本" in captured.out
