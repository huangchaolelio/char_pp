"""Feature-021 清洗规范文件加载器.

加载 ``src/config/curation_rubric/vN.yaml`` + jsonschema 校验，结果按版本号
``lru_cache`` 缓存（重启 API/worker 才清空，符合"启动期 + 任务排队前各做一次
schema 校验"约定）。

加载失败一律抛 ``AppException(RUBRIC_INVALID|RUBRIC_VERSION_NOT_FOUND)``，
路由层与提交服务统一兜住。

模块函数：

- :func:`load(version)`         — 加载并返回 :class:`CurationRubric`
- :func:`latest_version()`      — 扫描目录返回最高版本号字符串（``"v2"``/``"v1"``）
- :func:`list_available_versions()` — 全部版本号列表（按数字升序）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from src.api.errors import AppException, ErrorCode

logger = logging.getLogger(__name__)


# ── 包内绝对路径（项目根 / src/config/curation_rubric/） ──────────────────
# rubric_loader.py 位于 src/services/curation/；向上 3 级到项目根
_RUBRIC_DIR = Path(__file__).resolve().parents[2] / "config" / "curation_rubric"
_SCHEMA_PATH = _RUBRIC_DIR / "schema.json"

# 版本号文件名格式：vN.yaml（N 为 ≥1 整数）
_VERSION_FILENAME_RE = re.compile(r"^v(\d+)\.yaml$")


@dataclass(frozen=True)
class CurationRubric:
    """加载后的规范快照（不可变）。

    字段直接对应 ``src/config/curation_rubric/schema.json`` 顶层键；调用方
    通过 ``data`` 字典访问详细配置（如 ``data["thresholds"]["low_quality_ratio"]``）。
    """

    version: str
    description: str
    data: dict[str, Any]

    @property
    def threshold_accept(self) -> float:
        return float(self.data["thresholds"]["validity_score_accept"])

    @property
    def threshold_reject(self) -> float:
        return float(self.data["thresholds"]["validity_score_reject"])

    @property
    def low_quality_ratio(self) -> float:
        return float(self.data["thresholds"]["low_quality_ratio"])

    @property
    def short_video_seconds(self) -> int:
        return int(self.data["thresholds"]["short_video_seconds"])

    @property
    def min_segment_seconds(self) -> int:
        return int(self.data["thresholds"]["min_segment_seconds"])

    @property
    def llm_invoke_band(self) -> tuple[float, float]:
        lo, hi = self.data["llm_fallback"]["invoke_when_score_in"]
        return float(lo), float(hi)

    @property
    def llm_unavailable_decision(self) -> str:
        return str(self.data["llm_fallback"]["unavailable_decision"])

    @property
    def llm_enabled(self) -> bool:
        return bool(self.data["llm_fallback"]["enabled"])

    @property
    def llm_timeout_seconds(self) -> int:
        return int(self.data["llm_fallback"]["timeout_seconds"])

    @property
    def llm_prompt_template_path(self) -> str:
        return str(self.data["llm_fallback"]["prompt_template"])


# ── 内部辅助 ────────────────────────────────────────────────────────────


def _load_schema() -> dict[str, Any]:
    """加载并缓存 jsonschema 文件（jsonschema 自身不做 lru_cache，本函数兜住）."""
    return _load_schema_cached()


@lru_cache(maxsize=1)
def _load_schema_cached() -> dict[str, Any]:
    if not _SCHEMA_PATH.exists():
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=f"schema.json missing at {_SCHEMA_PATH}",
        )
    with _SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _file_path_for(version: str) -> Path:
    if not _VERSION_FILENAME_RE.match(f"{version}.yaml"):
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=f"invalid version literal {version!r} (expected 'vN' with N >= 1)",
        )
    return _RUBRIC_DIR / f"{version}.yaml"


# ── 公开 API ────────────────────────────────────────────────────────────


def list_available_versions() -> list[str]:
    """扫描目录返回所有合法 ``vN.yaml`` 的版本号字符串列表，按数字升序。

    无目录或无文件时返回空列表（不抛异常；让调用方决定是否报错）。
    """
    if not _RUBRIC_DIR.is_dir():
        return []
    versions: list[tuple[int, str]] = []
    for entry in _RUBRIC_DIR.iterdir():
        if not entry.is_file():
            continue
        m = _VERSION_FILENAME_RE.match(entry.name)
        if not m:
            continue
        versions.append((int(m.group(1)), f"v{m.group(1)}"))
    versions.sort(key=lambda x: x[0])
    return [v for _, v in versions]


def latest_version() -> str:
    """返回当前最高版本号字符串。无版本时抛 ``RUBRIC_VERSION_NOT_FOUND``。"""
    versions = list_available_versions()
    if not versions:
        raise AppException(
            ErrorCode.RUBRIC_VERSION_NOT_FOUND,
            message=f"no rubric files found in {_RUBRIC_DIR}",
        )
    return versions[-1]


@lru_cache(maxsize=8)
def load(version: str | None = None) -> CurationRubric:
    """加载指定版本的清洗规范（带 schema 校验 + 缓存）。

    Args:
        version: 形如 ``"v1"``；若为 ``None`` 取 :func:`latest_version`

    Returns:
        :class:`CurationRubric` — 不可变快照

    Raises:
        AppException(RUBRIC_VERSION_NOT_FOUND): 文件不存在
        AppException(RUBRIC_INVALID): YAML 解析失败 / schema 校验失败 /
            ``version`` 字段与文件名不匹配
    """
    if version is None:
        version = latest_version()

    path = _file_path_for(version)
    if not path.exists():
        raise AppException(
            ErrorCode.RUBRIC_VERSION_NOT_FOUND,
            message=f"rubric file not found: {path}",
            details={"version": version},
        )

    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=f"YAML parse error in {path.name}: {exc}",
            details={"version": version},
        )

    if not isinstance(data, dict):
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=f"rubric file {path.name} top-level must be a mapping",
            details={"version": version},
        )

    # 文件名 vs 顶层 version 字段一致性
    file_version = data.get("version")
    if file_version != version:
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=(
                f"version_filename_mismatch: file={path.name} but "
                f"top-level version={file_version!r}"
            ),
            details={"version": version, "file_version": file_version},
        )

    # jsonschema 校验
    schema = _load_schema()
    errors = sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: e.path)
    if errors:
        first = errors[0]
        path_repr = ".".join(str(p) for p in first.absolute_path) or "<root>"
        raise AppException(
            ErrorCode.RUBRIC_INVALID,
            message=f"schema validation failed at {path_repr}: {first.message}",
            details={
                "version": version,
                "schema_errors": [
                    {
                        "path": ".".join(str(p) for p in e.absolute_path) or "<root>",
                        "message": e.message,
                    }
                    for e in errors[:5]  # 最多保留 5 条以避免 details 巨大
                ],
            },
        )

    return CurationRubric(
        version=version,
        description=str(data.get("description") or ""),
        data=data,
    )


def reset_cache() -> None:
    """测试与运维场景下显式清空 ``lru_cache``（生产中重启 API/worker 即可）。"""
    load.cache_clear()
    _load_schema_cached.cache_clear()


__all__ = [
    "CurationRubric",
    "load",
    "latest_version",
    "list_available_versions",
    "reset_cache",
]
