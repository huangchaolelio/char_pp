#!/usr/bin/env python3
"""裸字符串错误码扫描 linter（Feature-017 阶段 6 T064）.

依据章程 v1.4.0 原则 IX：
    "错误码集中化：ErrorCode 枚举 + ERROR_STATUS_MAP + ERROR_DEFAULT_MESSAGE
     统一定义于 src/api/errors.py，作为单一事实来源。禁止在业务代码中使用
     裸字符串错误码（CI 扫描阻断）。"

扫描规则
--------
在 ``src/**/*.py`` 中查找裸字符串错误码形态：
  1. ``raise HTTPException(status_code=..., detail={"code": "XXX", ...})``
  2. ``return {"success": False, "error": {"code": "XXX", ...}}``
  3. 直接字面量 ``"code": "SOME_ERROR_CODE"``（任意上下文）

豁免
----
- ``src/api/errors.py``：ErrorCode 枚举定义处
- 测试文件：不扫描 ``tests/``（测试中断言字符串值是预期的）
- ``src/api/schemas/envelope.py``：ErrorBody/ErrorEnvelope 字段 ``code`` 的
  类型注解与默认值（非业务代码）

使用方式
--------
python scripts/lint_error_codes.py

退出码
------
- 0：全部合规
- 1：至少有一条违规；违规清单打印到 stderr
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ── 规则正则 ─────────────────────────────────────────────────────────────────

# 匹配形如 "code": "XXXXX_YYYY" 的裸字符串
_BARE_CODE_RE = re.compile(r'["\']code["\']\s*:\s*["\'][A-Z][A-Z0-9_]+["\']')

# 匹配 raise HTTPException（章程禁用，应改为 AppException）
_RAISE_HTTPEXC_RE = re.compile(r"\braise\s+HTTPException\b")


_EXEMPT_FILES = {
    "src/api/errors.py",          # ErrorCode 枚举定义
    "src/api/schemas/envelope.py",  # ErrorBody/ErrorEnvelope 字段定义
}


def _should_scan(path: Path) -> bool:
    rel = str(path).replace("\\", "/")
    # 仅扫描 src 目录
    if "/src/" not in f"/{rel}":
        return False
    # 豁免具体文件
    return not any(rel.endswith(e) for e in _EXEMPT_FILES)


def _scan_file(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    for i, line in enumerate(content.splitlines(), start=1):
        # 跳过注释行（简易判断：以 # 开头的行）
        if line.lstrip().startswith("#"):
            continue

        if _BARE_CODE_RE.search(line):
            violations.append(
                f"{path}:{i}: 裸字符串错误码（违反章程 v1.4.0 原则 IX）：{line.strip()}"
            )
        if _RAISE_HTTPEXC_RE.search(line):
            violations.append(
                f"{path}:{i}: 禁止直接 raise HTTPException（应改为 AppException）：{line.strip()}"
            )

    return violations


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    src_dir = project_root / "src"
    if not src_dir.exists():
        print(f"[lint] src 目录不存在：{src_dir}", file=sys.stderr)
        return 2

    all_violations: list[str] = []
    for py_file in src_dir.rglob("*.py"):
        if not _should_scan(py_file):
            continue
        all_violations.extend(_scan_file(py_file))

    if not all_violations:
        print("[lint] 错误码集中化校验通过 ✅（0 违规）")
        return 0

    print(f"[lint] 发现 {len(all_violations)} 条违规：\n", file=sys.stderr)
    for v in all_violations:
        print(f"  ✗ {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
