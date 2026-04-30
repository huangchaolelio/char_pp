"""Feature-018 — `specs/*/spec.md` 合规扫描.

校验目标：每份 ``specs/NNN-xxx/spec.md`` MUST 包含「业务阶段映射」小段，
并齐备六项子标签（见 ``scripts/audit/_spec_sections.py::REQUIRED_BUSINESS_STAGE_FIELDS``）。

静态排除：``scripts/audit/.scan-exclude.yml`` 列出的历史 17 个 Feature 目录。
新增 Feature 目录（019+）强制合规。

退出码：0（全部合规）| 1（至少一个 spec 违规）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.audit._spec_sections import (
    BUSINESS_STAGE_SECTION_HEADER,
    REQUIRED_BUSINESS_STAGE_FIELDS,
)
from scripts.audit.common import (
    emit_missing_field,
    emit_missing_section,
    get_changed_files,
    load_scan_exclude,
    repo_root,
)


def _collect_spec_files(excluded: set[Path]) -> list[Path]:
    root = repo_root() / "specs"
    if not root.exists():
        return []
    specs: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.resolve() in excluded:
            continue
        spec_md = entry / "spec.md"
        if spec_md.exists():
            specs.append(spec_md)
    return specs


def check_spec_file(spec_path: Path) -> list[str]:
    """检查单个 spec.md 是否合规；返回违规项列表（空 = 合规）.

    违规格式：``MISSING_SECTION: <rel_path> <section>`` 或
    ``MISSING_FIELD: <rel_path> <field>``。
    """
    violations: list[str] = []
    text = spec_path.read_text(encoding="utf-8")

    # 1) 查找「业务阶段映射」作为 markdown 标题（行首以 # 开头）；
    #    不允许把正文叙述中的文字当作段落开始（避免 Q-clarification 段误匹配）。
    lines = text.splitlines()
    section_start: int | None = None
    section_header_level: int = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        # 计算 # 数量
        hashes = len(stripped) - len(stripped.lstrip("#"))
        after = stripped[hashes:].lstrip()
        if after.startswith(BUSINESS_STAGE_SECTION_HEADER):
            section_start = i
            section_header_level = hashes
            break

    if section_start is None:
        violations.append(f"SECTION:{BUSINESS_STAGE_SECTION_HEADER}")
        emit_missing_section(spec_path, BUSINESS_STAGE_SECTION_HEADER)
        return violations

    # 2) 切片从 section_start 到下一个同级或更高级标题（# 数量 ≤ section_header_level）
    end = len(lines)
    for j in range(section_start + 1, len(lines)):
        ls = lines[j].lstrip()
        if ls.startswith("#"):
            hashes = len(ls) - len(ls.lstrip("#"))
            if hashes <= section_header_level and ls[hashes:].startswith(" "):
                end = j
                break
    section_text = "\n".join(lines[section_start:end])

    # 3) 六项子标签必须齐全
    for field in REQUIRED_BUSINESS_STAGE_FIELDS:
        if field not in section_text:
            violations.append(f"FIELD:{field}")
            emit_missing_field(spec_path, field)

    return violations


def scan_all() -> int:
    excluded = load_scan_exclude()
    specs = _collect_spec_files(excluded)
    total_violations = 0
    for spec in specs:
        v = check_spec_file(spec)
        total_violations += len(v)
    return total_violations


def scan_changed_only(commit_range: str | None) -> int:
    changed = get_changed_files(commit_range)
    if not changed:
        print("no target in diff")
        return 0
    root = repo_root()
    excluded = load_scan_exclude()
    target_specs: list[Path] = []
    for p in changed:
        if not p.exists():
            continue
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            continue
        rel_str = str(rel)
        if not rel_str.startswith("specs/") or not rel_str.endswith("/spec.md"):
            continue
        if p.resolve().parent in excluded:
            continue
        target_specs.append(p)
    if not target_specs:
        print("no target in diff")
        return 0

    total = 0
    for spec in target_specs:
        v = check_spec_file(spec)
        total += len(v)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Feature-018 specs/*/spec.md 合规扫描（业务阶段映射六项子标签）"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="全量扫描（默认）")
    mode.add_argument("--changed-only", action="store_true", help="仅扫本次变更的 spec.md")
    parser.add_argument(
        "--commit-range",
        default=None,
        help="git diff --name-only 的参数，默认 HEAD~1..HEAD",
    )
    args = parser.parse_args(argv)

    if args.changed_only:
        violations = scan_changed_only(args.commit_range)
    else:
        violations = scan_all()

    if violations:
        print(
            f"\n[spec_compliance] {violations} violation(s) detected; CI will block merge.",
            file=sys.stderr,
        )
        return 1
    print("[spec_compliance] all specs compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
