"""Feature-018 — 漂移扫描与合规扫描共享工具函数.

职责：
- ``load_scan_exclude()``：读取 ``.scan-exclude.yml`` 返回静态排除目录集合
- ``get_changed_files(commit_range)``：跑 git diff 获取变更文件列表
- ``emit_drift(kind, identifier, code_side, doc_side)``：统一输出格式
- ``parse_markdown_table_first_column(text, section_header)``：从 markdown 切片抽表格第一列
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_EXCLUDE_PATH = Path(__file__).resolve().parent / ".scan-exclude.yml"


def repo_root() -> Path:
    return _REPO_ROOT


def load_scan_exclude(path: Path | None = None) -> set[Path]:
    """读 .scan-exclude.yml 返回 exclude_paths（相对 repo root 的 Path 集合）."""
    p = path or _SCAN_EXCLUDE_PATH
    if not p.exists():
        return set()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    excluded = raw.get("exclude_paths", []) or []
    return {(_REPO_ROOT / item).resolve() for item in excluded}


def get_changed_files(commit_range: str | None = None) -> list[Path]:
    """运行 git diff --name-only 获取本地变更文件（相对 repo root）.

    当 commit_range 为 None 时默认使用 `HEAD~1..HEAD`（最近 1 commit）。
    本地环境/git 失败时返回空列表（由调用方决定是否告警）。
    """
    args = ["git", "diff", "--name-only"]
    if commit_range:
        args.append(commit_range)
    else:
        args.append("HEAD~1..HEAD")
    try:
        proc = subprocess.run(
            args,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    return [
        _REPO_ROOT / line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]


def emit_drift(
    kind: str,
    identifier: str,
    *,
    code_side: str | None,
    doc_side: str | None,
) -> None:
    """机器可读漂移行：``DRIFT: <kind> <identifier> code_side=<v> doc_side=<v>``."""
    print(
        f"DRIFT: {kind} {identifier}"
        f" code_side={code_side if code_side is not None else 'null'}"
        f" doc_side={doc_side if doc_side is not None else 'null'}"
    )


def emit_missing_section(spec_path: Path, section_or_field: str) -> None:
    """MISSING_SECTION / MISSING_FIELD 输出行."""
    try:
        rel = spec_path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        rel = spec_path  # 不在 repo 下（如测试 tmp_path），原样输出
    print(f"MISSING_SECTION: {rel} {section_or_field}")


def emit_missing_field(spec_path: Path, field_name: str) -> None:
    try:
        rel = spec_path.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        rel = spec_path
    print(f"MISSING_FIELD: {rel} {field_name}")


def parse_section(
    text: str,
    section_header: str,
    *,
    next_level_markers: Iterable[str] = ("## ", "### "),
) -> str:
    """抽取一个以 ``section_header`` 开头的段落，直到下一个同级或上级标题.

    ``section_header`` 需与文档中的实际标题字符串前缀一致，如 "### 7.4"。
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(section_header):
            start = i
            break
    if start is None:
        return ""

    # 从 start+1 开始找下一个标题（同级或更高级）
    header_marker = section_header.split(" ", 1)[0]  # "###"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j]
        # 若遇到同级或更高级标题则停止
        for m in next_level_markers:
            if stripped.startswith(m) and len(m.strip()) <= len(header_marker):
                end = j
                break
        else:
            continue
        break
    return "\n".join(lines[start:end])


_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")


def parse_markdown_table_first_column(section_text: str) -> list[str]:
    """从 markdown section 中抽第一张表格的第一列值（忽略表头 + 分隔行）."""
    rows: list[str] = []
    in_table = False
    header_captured = False
    for line in section_text.splitlines():
        if not line.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        if not header_captured:
            header_captured = True
            continue  # 表头行
        if re.match(r"^\|\s*-+\s*\|", line):
            continue  # 分隔行
        m = _TABLE_ROW_RE.match(line)
        if m:
            cell = m.group(1).strip()
            cell = cell.strip("`")  # 去掉反引号 markdown
            rows.append(cell)
    return rows


__all__ = [
    "repo_root",
    "load_scan_exclude",
    "get_changed_files",
    "emit_drift",
    "emit_missing_section",
    "emit_missing_field",
    "parse_section",
    "parse_markdown_table_first_column",
]
