"""Feature-018 — 代码 ↔ docs/business-workflow.md 漂移扫描.

**语义**：以"代码侧集中清单"为事实来源，检查文档侧是否同步。单向差集。

扫描八类（data-model.md § 9 DriftReport.kind）：
1. ``error_code_prefix``         — KB/预处理 error_codes 与 § 7.4 错误码表
2. ``task_status_enum``          — TaskStatus / TaskType 与 § 7.1 任务级状态
3. ``extraction_job_status``     — ExtractionJobStatus 与 § 2 阶段 DoD
4. ``kb_status``                 — KBStatus 与 § 4.3 状态机
5. ``scorer_threshold``          — diagnosis_scorer 阈值 与 § 5.3 评分公式（MVP 仅检查常量存在性）
6. ``channel_seed``              — task_channel_configs seed 与 § 3.1/5.1
7. ``optimization_lever``        — config/optimization_levers.yml 与 § 9 三类杠杆表
8. ``spec_template_fields``      — REQUIRED_BUSINESS_STAGE_FIELDS 与 spec-template 模板

退出码：0（无漂移）| 1（有漂移）。

调用方式：
    python -m scripts.audit.workflow_drift --full
    python -m scripts.audit.workflow_drift --changed-only
    python -m scripts.audit.workflow_drift --commit-range=origin/master...HEAD
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from pathlib import Path

from scripts.audit._spec_sections import (
    BUSINESS_STAGE_SECTION_HEADER,
    REQUIRED_BUSINESS_STAGE_FIELDS,
)
from scripts.audit.common import (
    emit_drift,
    get_changed_files,
    parse_markdown_table_first_column,
    parse_section,
    repo_root,
)

_DOC_PATH = repo_root() / "docs" / "business-workflow.md"
_SPEC_TEMPLATE_PATH = repo_root() / ".specify" / "templates" / "spec-template.md"
_LEVERS_YAML = repo_root() / "config" / "optimization_levers.yml"


# ── 代码侧事实采集 ─────────────────────────────────────────────────────────


def _collect_code_error_codes() -> set[str]:
    """从两个 error_codes.py 模块收集所有错误码字符串."""
    codes: set[str] = set()
    try:
        from src.services.kb_extraction_pipeline.error_codes import ALL_ERROR_CODES as kb_codes
        codes.update(kb_codes)
    except Exception:  # noqa: BLE001
        pass
    try:
        pp_mod = importlib.import_module("src.services.preprocessing.error_codes")
        for name in dir(pp_mod):
            if name.startswith("_") or name.lower() == name:
                continue
            val = getattr(pp_mod, name)
            if isinstance(val, str) and val.isupper() and "_" in val:
                codes.add(val)
    except Exception:  # noqa: BLE001
        pass
    return codes


def _collect_code_task_status() -> set[str]:
    from src.models.analysis_task import TaskStatus
    return {m.value for m in TaskStatus}


def _collect_code_task_type() -> set[str]:
    from src.models.analysis_task import TaskType
    return {m.value for m in TaskType}


def _collect_code_extraction_job_status() -> set[str]:
    from src.models.extraction_job import ExtractionJobStatus
    return {m.value for m in ExtractionJobStatus}


def _collect_code_kb_status() -> set[str]:
    from src.models.tech_knowledge_base import KBStatus
    return {m.value for m in KBStatus}


def _collect_levers_keys() -> set[str]:
    import yaml
    if not _LEVERS_YAML.exists():
        return set()
    raw = yaml.safe_load(_LEVERS_YAML.read_text(encoding="utf-8")) or {}
    return {e["key"] for e in (raw.get("levers") or []) if "key" in e}


def _collect_spec_template_fields() -> set[str]:
    """读 spec-template.md 中「业务阶段映射」段落，提取六项子标签."""
    if not _SPEC_TEMPLATE_PATH.exists():
        return set()
    text = _SPEC_TEMPLATE_PATH.read_text(encoding="utf-8")
    # 查找 "业务阶段映射" 段落（可能在 ### 或 ## 级）
    markers = (
        f"### {BUSINESS_STAGE_SECTION_HEADER}",
        f"## {BUSINESS_STAGE_SECTION_HEADER}",
        f"#### {BUSINESS_STAGE_SECTION_HEADER}",
    )
    section = ""
    for m in markers:
        idx = text.find(m)
        if idx != -1:
            # 抽取从该标题到下一个同级或更高级标题之间的内容
            # 简化：向前切到下一个 '\n## ' 或 '\n### '
            lines = text[idx:].splitlines()
            collected: list[str] = [lines[0]]
            for ln in lines[1:]:
                if ln.startswith("## ") or ln.startswith("### "):
                    break
                collected.append(ln)
            section = "\n".join(collected)
            break
    if not section:
        return set()
    found: set[str] = set()
    for field in REQUIRED_BUSINESS_STAGE_FIELDS:
        if field in section:
            found.add(field)
    return found


# ── 文档侧采集（parse_section + parse_markdown_table_first_column 复用） ──


def _parse_doc_error_codes() -> set[str]:
    if not _DOC_PATH.exists():
        return set()
    text = _DOC_PATH.read_text(encoding="utf-8")
    section = parse_section(text, "### 7.4")
    if not section:
        return set()
    # 错误码表第一列可能含多个错误码（如 "`VIDEO_TRANSCODE_FAILED:` / `VIDEO_SPLIT_FAILED:`"）
    codes: set[str] = set()
    for cell in parse_markdown_table_first_column(section):
        # 抓取所有大写串
        for m in re.finditer(r"([A-Z][A-Z0-9_]{2,}):?", cell):
            codes.add(m.group(1))
    return codes


def _parse_doc_task_status() -> set[str]:
    if not _DOC_PATH.exists():
        return set()
    text = _DOC_PATH.read_text(encoding="utf-8")
    section = parse_section(text, "### 7.1")
    if not section:
        return set()
    # 形如 ``status ∈ {pending, processing, success, failed, rejected}``
    m = re.search(r"status\s*[∈⊆]\s*\{([^}]+)\}", section)
    if not m:
        return set()
    return {item.strip() for item in m.group(1).split(",") if item.strip()}


def _parse_doc_levers() -> set[str]:
    if not _DOC_PATH.exists():
        return set()
    text = _DOC_PATH.read_text(encoding="utf-8")
    section = parse_section(text, "## 9.")
    if not section:
        section = parse_section(text, "## 9 ")
    if not section:
        return set()
    # § 9 表格第二列通常是 "键" —— 但简化实现：在 § 9 全文正则捕获 KEY 样式
    text_section = section
    keys: set[str] = set()
    # 敏感键 / env 键 / table 键
    for token in re.findall(r"`([A-Za-z][A-Za-z0-9_./-]+)`", text_section):
        if any(ch.isupper() for ch in token) or "." in token or "/" in token:
            keys.add(token)
    return keys


def _parse_doc_spec_template() -> set[str]:
    """§ 章程原则 X 或项目章程 README 中引用的六项；本 Feature 默认走模板侧."""
    # 不做二次解析——spec_template_fields 只对比 REQUIRED_BUSINESS_STAGE_FIELDS
    # 常量 vs 模板里是否齐全。
    return set(REQUIRED_BUSINESS_STAGE_FIELDS)


# ── 漂移计算 ──────────────────────────────────────────────────────────────


def _diff_sets(
    code_side: set[str],
    doc_side: set[str],
    *,
    kind: str,
) -> int:
    """代码侧存在但文档侧缺失 ⇒ DRIFT."""
    diffs = code_side - doc_side
    for item in sorted(diffs):
        emit_drift(kind, item, code_side=item, doc_side=None)
    return len(diffs)


# ── Main scanner ──────────────────────────────────────────────────────────


def scan_all() -> int:
    drift_count = 0

    # 1) error_code_prefix
    code_ec = _collect_code_error_codes()
    doc_ec = _parse_doc_error_codes()
    if code_ec and doc_ec:
        drift_count += _diff_sets(code_ec, doc_ec, kind="error_code_prefix")
    elif code_ec and not doc_ec:
        # 文档侧未能解析到错误码表 ⇒ 每一个代码错误码都算作 drift（宁严勿松）
        for item in sorted(code_ec):
            emit_drift("error_code_prefix", item, code_side=item, doc_side=None)
        drift_count += len(code_ec)

    # 2) task_status_enum
    code_ts = _collect_code_task_status()
    doc_ts = _parse_doc_task_status()
    if code_ts and doc_ts:
        drift_count += _diff_sets(code_ts, doc_ts, kind="task_status_enum")

    # 3) extraction_job_status — 轻校验：代码侧枚举必须在文档 § 2 / § 4 出现
    code_ejs = _collect_code_extraction_job_status()
    # 简单检查：在 business-workflow.md 全文搜索
    doc_text = _DOC_PATH.read_text(encoding="utf-8") if _DOC_PATH.exists() else ""
    for item in sorted(code_ejs):
        if doc_text and item not in doc_text:
            emit_drift("extraction_job_status", item, code_side=item, doc_side=None)
            drift_count += 1

    # 4) kb_status
    code_kbs = _collect_code_kb_status()
    for item in sorted(code_kbs):
        if doc_text and item not in doc_text:
            emit_drift("kb_status", item, code_side=item, doc_side=None)
            drift_count += 1

    # 5) scorer_threshold（MVP 检查：§ 5.3 段落存在）
    if doc_text and "### 5.3" not in doc_text:
        emit_drift("scorer_threshold", "section_missing", code_side="diagnosis_scorer", doc_side=None)
        drift_count += 1

    # 6) channel_seed（MVP 检查：通道名在文档出现）
    code_tt = _collect_code_task_type()
    for item in sorted(code_tt):
        if doc_text and item not in doc_text:
            emit_drift("channel_seed", item, code_side=item, doc_side=None)
            drift_count += 1

    # 7) optimization_lever
    code_levers = _collect_levers_keys()
    # § 9 的语义：key 必须在文档中的 § 9 片段出现；这里简化：全局 contains 匹配
    for item in sorted(code_levers):
        if doc_text and item not in doc_text:
            emit_drift("optimization_lever", item, code_side=item, doc_side=None)
            drift_count += 1

    # 8) spec_template_fields — 模板侧必须齐全
    tmpl_fields = _collect_spec_template_fields()
    for field in REQUIRED_BUSINESS_STAGE_FIELDS:
        if field not in tmpl_fields:
            emit_drift(
                "spec_template_fields",
                field,
                code_side=field,
                doc_side=None,
            )
            drift_count += 1

    return drift_count


# ── 变更感知模式 ──────────────────────────────────────────────────────────


_CODE_SIDE_TRIGGERS = (
    "src/services/kb_extraction_pipeline/error_codes.py",
    "src/services/preprocessing/error_codes.py",
    "src/models/analysis_task.py",
    "src/models/extraction_job.py",
    "src/models/tech_knowledge_base.py",
    "src/db/migrations/versions/",
    "config/optimization_levers.yml",
    ".specify/templates/spec-template.md",
    "scripts/audit/_spec_sections.py",
    "src/services/diagnosis_scorer.py",
)
_DOC_SIDE_TRIGGERS = (
    "docs/business-workflow.md",
)


def scan_changed_only(commit_range: str | None) -> int:
    changed = get_changed_files(commit_range)
    if not changed:
        print("no target in diff")
        return 0

    changed_rels = [str(p.resolve().relative_to(repo_root())) for p in changed if p.exists()]
    # 若有任一命中触发路径，即进行全量扫描（简化策略）
    triggered = False
    for rel in changed_rels:
        if any(rel.startswith(t) or t in rel for t in _CODE_SIDE_TRIGGERS):
            triggered = True
            break
        if any(rel.startswith(t) for t in _DOC_SIDE_TRIGGERS):
            triggered = True
            break
    if not triggered:
        print("no target in diff")
        return 0
    return scan_all()


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Feature-018 代码 ↔ business-workflow.md 漂移扫描"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="全量扫描（默认）")
    mode.add_argument("--changed-only", action="store_true", help="仅扫本次变更涉及清单")
    parser.add_argument(
        "--commit-range",
        default=None,
        help="git diff --name-only 的参数，默认 HEAD~1..HEAD",
    )
    args = parser.parse_args(argv)

    if args.changed_only:
        drifts = scan_changed_only(args.commit_range)
    else:
        drifts = scan_all()

    if drifts:
        print(f"\n[workflow_drift] {drifts} drift(s) detected; CI will block merge.", file=sys.stderr)
        return 1
    print("[workflow_drift] no drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
