#!/usr/bin/env python3
"""API 命名规范 linter（Feature-017 阶段 5 T051）.

依据章程 v1.4.0 原则 IX + 用户规则 1「API 设计规范」：

校验规则
--------
1. **路径前缀**：所有路径必须以 ``/api/v1/`` 开头
2. **资源段 kebab-case**：路径静态段使用小写 + 连字符（如 ``teaching-tips``）
3. **ID 路径参数命名**：``{id}`` / ``{xxx-id}`` 一律不允许，必须形如
   ``{resource_id}``（下划线分隔，名词前缀 + ``_id`` 后缀）
4. **分页参数**：凡返回 ``SuccessEnvelope[list[...]]`` 的端点必须接受
   ``page`` 与 ``page_size`` 两个 query 参数
5. **禁用 limit/offset/skip/take**：列表端点不得再出现上述旧参数

使用方式
--------
# 默认读取本地运行中 FastAPI 的 /openapi.json（http://localhost:8080）：
python scripts/lint_api_naming.py

# 或离线读取已保存的 openapi.json 文件：
python scripts/lint_api_naming.py --file /tmp/openapi.json

退出码
------
- 0：全部合规
- 1：至少有一条违规；违规清单打印到 stderr
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ── 规则正则 ─────────────────────────────────────────────────────────────────

_SEGMENT_KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_ID_PARAM_RE = re.compile(r"^\{[a-z][a-z0-9]*_id\}$")
_ENUM_PARAM_RE = re.compile(r"^\{[a-z][a-z0-9]*(_[a-z0-9]+)*\}$")  # snake_case 枚举型业务参数
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")
_FORBIDDEN_PAGE_PARAMS = {"limit", "offset", "skip", "take", "pageNum", "pageSize"}

# 豁免路径（非 /api/v1 业务 API，章程不约束）
_EXEMPT_PATHS: set[str] = {"/health", "/", "/docs", "/redoc", "/openapi.json"}


def _load_openapi(source: str | None) -> dict[str, Any]:
    if source:
        path = Path(source)
        if not path.exists():
            print(f"[lint] openapi 文件不存在：{path}", file=sys.stderr)
            sys.exit(2)
        return json.loads(path.read_text(encoding="utf-8"))

    # 默认从运行中的服务拉取（不依赖 requests，直接 urllib）
    import urllib.request

    url = "http://localhost:8080/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[lint] 无法从 {url} 获取 openapi.json：{exc}\n"
            f"       请确保服务运行中，或用 --file 指定离线文件。",
            file=sys.stderr,
        )
        sys.exit(2)


def _check_path_naming(path: str) -> list[str]:
    """校验路径静态段 + ID 参数段命名。返回违规消息列表。"""
    violations: list[str] = []

    if path in _EXEMPT_PATHS:
        return violations

    if not path.startswith("/api/v1/"):
        violations.append(f"路径未以 /api/v1/ 开头：{path}")
        return violations

    segments = path[len("/api/v1/"):].split("/")
    for seg in segments:
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            # 路径参数段：允许两种合法形态
            #   (a) 资源 ID 形式：{resource_id}（下划线 + _id 后缀）
            #   (b) 业务枚举/标识形式：{task_type}/{tech_category}/{version} 等
            #       —— 必须是 snake_case；上游代码约定非 UUID/数字类路径参数用业务
            #       名词而非通用 {id}
            if not _ENUM_PARAM_RE.match(seg):
                violations.append(
                    f"路径参数段命名不符合 snake_case：{seg}（path={path}）"
                )
            elif seg == "{id}":
                violations.append(
                    f"禁止使用通用 {{id}} 路径参数，请改为 {{resource_id}} 形式（path={path}）"
                )
        else:
            if not _SEGMENT_KEBAB_RE.match(seg):
                violations.append(
                    f"路径段必须为 kebab-case 小写，实际：'{seg}'（path={path}）"
                )

    return violations


def _is_list_endpoint(operation: dict[str, Any]) -> bool:
    """通过 response_model 引用判定端点是否为列表类型。"""
    resp_200 = operation.get("responses", {}).get("200", {})
    schema = (
        resp_200.get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    # FastAPI 会把 SuccessEnvelope[list[T]] 转为 $ref 指向一个以 "list" / "_list"
    # 结尾的组合类；或在 title 中包含 "List"。也兼容直接看 data 字段是否 array。
    schema_str = json.dumps(schema, ensure_ascii=False)
    return (
        '"type": "array"' in schema_str
        or "List[" in schema_str
        or "list[" in schema_str
    )


def _check_pagination_params(
    path: str, method: str, operation: dict[str, Any]
) -> list[str]:
    violations: list[str] = []
    if method.lower() != "get":
        return violations
    if not _is_list_endpoint(operation):
        return violations

    query_names = {
        p["name"]
        for p in operation.get("parameters", [])
        if p.get("in") == "query"
    }

    for forbidden in _FORBIDDEN_PAGE_PARAMS:
        if forbidden in query_names:
            violations.append(
                f"列表端点禁用 ``{forbidden}`` 参数，请改用 page/page_size（{method.upper()} {path}）"
            )

    missing: list[str] = []
    if "page" not in query_names:
        missing.append("page")
    if "page_size" not in query_names:
        missing.append("page_size")
    if missing:
        violations.append(
            f"列表端点缺少分页参数 {missing}（{method.upper()} {path}）"
        )

    return violations


def lint(spec: dict[str, Any]) -> list[str]:
    all_violations: list[str] = []
    paths: dict[str, Any] = spec.get("paths", {})
    for path, methods in paths.items():
        # 1) 路径命名
        all_violations.extend(_check_path_naming(path))
        # 2) 分页参数
        for method, op in methods.items():
            if method in {"parameters", "summary", "description"}:
                continue
            if not isinstance(op, dict):
                continue
            all_violations.extend(_check_pagination_params(path, method, op))
    return all_violations


def main() -> int:
    parser = argparse.ArgumentParser(description="API 命名规范 linter（Feature-017 T051）")
    parser.add_argument(
        "--file", help="离线 openapi.json 路径（默认从 localhost:8080 拉取）"
    )
    args = parser.parse_args()

    spec = _load_openapi(args.file)
    violations = lint(spec)

    if not violations:
        print("[lint] API 命名规范校验通过 ✅（0 违规）")
        return 0

    print(f"[lint] 发现 {len(violations)} 条违规：\n", file=sys.stderr)
    for v in violations:
        print(f"  ✗ {v}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
