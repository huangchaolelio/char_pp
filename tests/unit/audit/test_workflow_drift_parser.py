"""Feature-018 T030 — workflow_drift 脚本 markdown 解析单元测试.

覆盖：
- parse_section：能从 business-workflow.md 抽取单一小节
- parse_markdown_table_first_column：抽表格第一列且去反引号
- 错误码表含 ``VIDEO_TRANSCODE_FAILED: / VIDEO_SPLIT_FAILED:`` 时能分离提取
"""

from __future__ import annotations

import textwrap

import pytest

from scripts.audit.common import (
    parse_markdown_table_first_column,
    parse_section,
)


def test_parse_section_extracts_single_subsection():
    md = textwrap.dedent("""
        ## 7. 可观测体系

        ### 7.4 错误码

        | 前缀 | 语义 |
        |-----|------|
        | `FOO:` | bar |

        ### 7.5 另一节

        这里应该不被包含
    """)
    section = parse_section(md, "### 7.4")
    assert "FOO:" in section
    assert "这里应该不被包含" not in section


def test_parse_section_returns_empty_when_missing():
    md = "some random text with no section headers"
    section = parse_section(md, "### 99.9")
    assert section == ""


def test_parse_markdown_table_first_column_basic():
    md = textwrap.dedent("""
        | 错误码前缀 | 语义 | 重试 |
        |-----------|------|------|
        | `VIDEO_QUALITY_REJECTED:` | fps不过关 | 否 |
        | `POSE_NO_KEYPOINTS:` | 找不到骨架 | 否 |
    """)
    cells = parse_markdown_table_first_column(md)
    assert cells == ["VIDEO_QUALITY_REJECTED:", "POSE_NO_KEYPOINTS:"]


def test_parse_handles_slash_separated_codes_via_regex():
    """``VIDEO_TRANSCODE_FAILED: / VIDEO_SPLIT_FAILED:`` — workflow_drift 用正则提取，不是此函数."""
    md = textwrap.dedent("""
        | 错误码 | 语义 |
        |-------|------|
        | `VIDEO_TRANSCODE_FAILED:` / `VIDEO_SPLIT_FAILED:` | ffmpeg 失败 |
    """)
    cells = parse_markdown_table_first_column(md)
    # parse_markdown_table_first_column 把 raw 字符串整个返回（去掉最外层反引号）
    assert len(cells) == 1
    # 在 workflow_drift 里通过正则再进一步切分
    import re
    codes = re.findall(r"([A-Z][A-Z0-9_]{2,}):?", cells[0])
    assert "VIDEO_TRANSCODE_FAILED" in codes
    assert "VIDEO_SPLIT_FAILED" in codes
