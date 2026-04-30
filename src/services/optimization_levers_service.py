"""Feature-018 — 优化杠杆统一台账服务（US3）.

对齐:
- specs/018-workflow-standardization/spec.md FR-013 ~ FR-016
- data-model.md § 6（YAML schema）+ § 8（响应 DTO）
- contracts/admin-levers.yaml

核心职责:
1. ``__init__``：加载并校验 ``config/optimization_levers.yml`` (fail-fast)；
   违规抛 ``AppException(OPTIMIZATION_LEVERS_YAML_INVALID)``。
2. ``list_levers(session, phase)``：对每条条目按 source 策略读当前值：
   - db_table：查 ``task_channel_configs.*.updated_at / updated_by``
   - env：读环境变量或 ``get_settings()``
   - config_file：读文件 sha256 摘要
   敏感键 ``current_value = None`` / ``is_configured = bool(value)``；
   非敏感键 ``current_value = 实际值`` / ``is_configured = None``。
3. 不引入新表；``last_changed_at / last_changed_by`` 从既有列 / ``git log`` 兜底。
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode
from src.api.schemas.admin_levers import LeverEntry, LeverGroups
from src.models.analysis_task import BusinessPhase, TaskType
from src.models.task_channel_config import TaskChannelConfig

logger = logging.getLogger(__name__)

# ── 合法取值集合（schema 校验用） ─────────────────────────────────────────
_ALLOWED_TYPE = {"runtime_params", "algorithm_models", "rules_prompts"}
_ALLOWED_SOURCE = {"db_table", "env", "config_file"}
_ALLOWED_RESTART_SCOPE = {"none", "worker", "api"}
_ALLOWED_PHASE = {"TRAINING", "STANDARDIZATION", "INFERENCE"}


class OptimizationLeversService:
    """优化杠杆台账只读服务."""

    _DEFAULT_YAML_PATH = Path("config/optimization_levers.yml")

    def __init__(self, yaml_path: Path | None = None) -> None:
        self._yaml_path = yaml_path or self._DEFAULT_YAML_PATH
        self._levers: list[dict[str, Any]] = self._load_and_validate()

    # ─────────────────────────── 加载 + 校验 ─────────────────────────────

    def _load_and_validate(self) -> list[dict[str, Any]]:
        """加载 YAML 并做 schema 校验；失败 fail-fast 抛 AppException."""
        if not self._yaml_path.exists():
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message=f"optimization_levers.yml 文件不存在: {self._yaml_path}",
                details={"path": str(self._yaml_path)},
            )

        try:
            raw = yaml.safe_load(self._yaml_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message=f"YAML 解析失败: {exc}",
                details={"reason": str(exc)},
            ) from exc

        if not isinstance(raw, dict) or "levers" not in raw:
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message="YAML 顶层缺少 levers 键",
                details={"reason": "missing_top_level_levers"},
            )

        levers = raw["levers"]
        if not isinstance(levers, list) or not levers:
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message="levers 必须为非空列表",
                details={"reason": "empty_or_not_list"},
            )

        seen_keys: set[str] = set()
        for entry in levers:
            self._validate_entry(entry, seen_keys)
        return levers

    def _validate_entry(
        self, entry: Any, seen_keys: set[str]
    ) -> None:
        if not isinstance(entry, dict):
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message="lever 条目必须为对象",
                details={"reason": "entry_not_object"},
            )

        def _raise(reason: str, offending_key: str | None = None) -> None:
            details: dict[str, Any] = {"reason": reason}
            if offending_key is not None:
                details["offending_key"] = offending_key
            raise AppException(
                ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID,
                message=reason,
                details=details,
            )

        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            _raise("每个 lever 必须含有非空 key 字符串")
        if key in seen_keys:
            _raise(f"lever.key 全局唯一约束违反: {key}", offending_key=key)
        seen_keys.add(key)

        t = entry.get("type")
        if t not in _ALLOWED_TYPE:
            _raise(
                f"type 必须为 {sorted(_ALLOWED_TYPE)} 之一（当前: {t!r}）",
                offending_key=key,
            )

        src = entry.get("source")
        if src not in _ALLOWED_SOURCE:
            _raise(
                f"source 必须为 {sorted(_ALLOWED_SOURCE)} 之一（当前: {src!r}）",
                offending_key=key,
            )

        rs = entry.get("restart_scope")
        if rs not in _ALLOWED_RESTART_SCOPE:
            _raise(
                f"restart_scope 必须为 {sorted(_ALLOWED_RESTART_SCOPE)} 之一（当前: {rs!r}）",
                offending_key=key,
            )

        phase_list = entry.get("business_phase")
        if not isinstance(phase_list, list) or not phase_list:
            _raise("business_phase 必须为非空列表", offending_key=key)
        for p in phase_list:
            if p not in _ALLOWED_PHASE:
                _raise(
                    f"business_phase 含非法值: {p!r}",
                    offending_key=key,
                )

        if "sensitive" not in entry or not isinstance(entry["sensitive"], bool):
            _raise("sensitive 必须为 bool", offending_key=key)

    # ─────────────────────────── 查询入口 ───────────────────────────────

    async def list_levers(
        self,
        session: AsyncSession,
        phase: BusinessPhase | None = None,
    ) -> LeverGroups:
        """按三类分组返回所有杠杆台账；可按阶段过滤."""
        # 预加载 task_channel_configs 全量到内存（N≤5）
        channels = await self._load_channel_configs(session)

        groups: dict[str, list[LeverEntry]] = {
            "runtime_params": [],
            "algorithm_models": [],
            "rules_prompts": [],
        }

        for entry in self._levers:
            if phase is not None and phase.value not in entry["business_phase"]:
                continue

            lever_entry = self._build_lever_entry(entry, channels)
            groups[entry["type"]].append(lever_entry)

        return LeverGroups(**groups)

    async def _load_channel_configs(
        self, session: AsyncSession
    ) -> dict[str, TaskChannelConfig]:
        rows = (
            await session.execute(select(TaskChannelConfig))
        ).scalars().all()
        return {c.task_type.value: c for c in rows}

    def _build_lever_entry(
        self,
        entry: dict[str, Any],
        channels: dict[str, TaskChannelConfig],
    ) -> LeverEntry:
        key: str = entry["key"]
        sensitive: bool = entry["sensitive"]
        source: str = entry["source"]

        current_value: Any = None
        last_changed_at: datetime | None = None
        last_changed_by: str | None = None
        is_configured: bool | None = None

        if source == "db_table":
            current_value, last_changed_at, last_changed_by = (
                self._resolve_db_table_value(key, channels)
            )
        elif source == "env":
            raw = os.environ.get(key, "")
            if sensitive:
                is_configured = bool(raw)
                # current_value 保持 None；last_changed_* 恒 None
            else:
                current_value = raw or None
        elif source == "config_file":
            current_value, last_changed_at, last_changed_by = (
                self._resolve_config_file_value(key)
            )

        if sensitive:
            # 敏感键只返回 is_configured；清洗其它字段
            raw_value = os.environ.get(key, "") if source == "env" else current_value
            is_configured = bool(raw_value)
            current_value = None
            last_changed_at = None
            last_changed_by = None

        return LeverEntry(
            key=key,
            type=entry["type"],
            source=source,  # type: ignore[arg-type]
            effective_in_seconds=entry.get("effective_in_seconds"),
            restart_scope=entry["restart_scope"],
            business_phase=entry["business_phase"],
            current_value=current_value,
            last_changed_at=last_changed_at,
            last_changed_by=last_changed_by,
            is_configured=is_configured,
        )

    def _resolve_db_table_value(
        self,
        key: str,
        channels: dict[str, TaskChannelConfig],
    ) -> tuple[Any, datetime | None, str | None]:
        """解析 key 形如 ``task_channel_configs.<task_type>.<field>``."""
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != "task_channel_configs":
            return None, None, None
        _, task_type_str, field = parts
        cfg = channels.get(task_type_str)
        if cfg is None:
            return None, None, None
        value = getattr(cfg, field, None)
        updated_at = getattr(cfg, "updated_at", None)
        updated_by = getattr(cfg, "updated_by", None)
        return value, updated_at, updated_by

    def _resolve_config_file_value(
        self, key: str
    ) -> tuple[str | None, datetime | None, str | None]:
        """读取文件 sha256 摘要 + git log 获取最后修改人."""
        path = Path(key)
        if not path.exists():
            return None, None, None
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:12]
            value = f"loaded (hash=sha256:{digest}..)"
        except OSError as exc:
            logger.warning("config_file 读取失败 key=%s: %s", key, exc)
            return None, None, None

        last_changed_at = None
        last_changed_by = None
        try:
            proc = subprocess.run(
                ["git", "log", "-1", "--format=%aI|%an", "--", str(path)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts = proc.stdout.strip().split("|", 1)
                if len(parts) == 2:
                    try:
                        last_changed_at = datetime.fromisoformat(parts[0])
                    except ValueError:
                        last_changed_at = None
                    last_changed_by = parts[1] or None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("git log 查询失败（降级 null）: %s", exc)

        return value, last_changed_at, last_changed_by


__all__ = ["OptimizationLeversService"]
