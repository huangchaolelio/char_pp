"""Feature-018 T031 — spec_compliance 脚本合规检查单元测试.

覆盖：
- spec.md 含「业务阶段映射」段 + 六项子标签齐全 ⇒ 零违规
- spec.md 完全不含「业务阶段映射」段 ⇒ MISSING_SECTION
- spec.md 含段但缺 3 项子标签 ⇒ 3 条 MISSING_FIELD
- spec.md 含段但段名错别字 ⇒ MISSING_SECTION
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from scripts.audit.spec_compliance import check_spec_file


def _write_spec(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "spec.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_spec_no_violations(tmp_path: Path):
    spec = _write_spec(
        tmp_path,
        textwrap.dedent("""
            # 功能规范

            ### 业务阶段映射 *(必填)*

            - **所属阶段**: TRAINING
            - **所属步骤**: extract_kb
            - **DoD 引用**: § 2 阶段 DoD
            - **可观测锚点**: § 7.1
            - **章程级约束影响**: 无
            - **回滚剧本**: alembic downgrade -1

            ## 后续章节
        """),
    )
    violations = check_spec_file(spec)
    assert violations == []


def test_missing_section(tmp_path: Path):
    spec = _write_spec(
        tmp_path,
        "# 功能规范\n\n## 用户场景\n\n（此 spec 没有业务阶段映射段）",
    )
    violations = check_spec_file(spec)
    assert len(violations) == 1
    assert violations[0].startswith("SECTION:")


def test_section_with_missing_fields(tmp_path: Path):
    spec = _write_spec(
        tmp_path,
        textwrap.dedent("""
            # 功能规范

            ### 业务阶段映射

            - **所属阶段**: TRAINING
            - **所属步骤**: extract_kb
            - **DoD 引用**: § 2

            ## 后续章节
        """),
    )
    # 缺 3 项：可观测锚点、章程级约束影响、回滚剧本
    violations = check_spec_file(spec)
    field_vios = [v for v in violations if v.startswith("FIELD:")]
    assert len(field_vios) == 3


def test_typo_in_section_header_treated_as_missing(tmp_path: Path):
    spec = _write_spec(
        tmp_path,
        textwrap.dedent("""
            # 功能规范

            ### 业务阶段映照   <!-- 错别字 -->

            - **所属阶段**: ...
        """),
    )
    violations = check_spec_file(spec)
    assert any(v.startswith("SECTION:") for v in violations)
