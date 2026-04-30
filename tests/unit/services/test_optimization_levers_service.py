"""Feature-018 T041 — OptimizationLeversService 单元测试.

覆盖：
- 合法 YAML 加载成功（默认 config/optimization_levers.yml）
- 敏感键返回 ``is_configured`` 但不含 ``current_value``
- 非敏感键返回 ``current_value``
- Schema 违规（type / source / restart_scope 非法）⇒
  ``AppException(OPTIMIZATION_LEVERS_YAML_INVALID)``
- YAML 文件不存在 ⇒ 同上
- ``phase`` 过滤正确
- 无敏感泄露：``LeverEntry.model_dump`` 中敏感键的 ``current_value`` 始终为 None (T055)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.errors import AppException, ErrorCode
from src.models.analysis_task import BusinessPhase
from src.services.optimization_levers_service import OptimizationLeversService


@pytest.fixture
def valid_yaml_path(tmp_path: Path) -> Path:
    yml = tmp_path / "levers.yml"
    yml.write_text(
        """
version: 1
levers:
  - key: task_channel_configs.kb_extraction.concurrency
    type: runtime_params
    source: db_table
    effective_in_seconds: 30
    restart_scope: none
    business_phase: [TRAINING]
    sensitive: false

  - key: POSE_BACKEND
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, INFERENCE]
    sensitive: false

  - key: VENUS_TOKEN
    type: algorithm_models
    source: env
    effective_in_seconds: null
    restart_scope: worker
    business_phase: [TRAINING, INFERENCE]
    sensitive: true
""",
        encoding="utf-8",
    )
    return yml


def _fake_session(channel_configs: list | None = None) -> MagicMock:
    session = MagicMock()
    # mock session.execute(select(...)) -> Result with .scalars().all()
    res = MagicMock()
    res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=channel_configs or [])))
    session.execute = AsyncMock(return_value=res)
    return session


def test_load_valid_yaml(valid_yaml_path: Path) -> None:
    svc = OptimizationLeversService(yaml_path=valid_yaml_path)
    assert len(svc._levers) == 3


def test_missing_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(AppException) as exc_info:
        OptimizationLeversService(yaml_path=tmp_path / "nonexistent.yml")
    assert exc_info.value.code == ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID


def test_invalid_type_field_raises(tmp_path: Path) -> None:
    yml = tmp_path / "bad.yml"
    yml.write_text(
        """
version: 1
levers:
  - key: FOO
    type: illegal_type
    source: env
    restart_scope: worker
    business_phase: [TRAINING]
    sensitive: false
""",
        encoding="utf-8",
    )
    with pytest.raises(AppException) as exc_info:
        OptimizationLeversService(yaml_path=yml)
    assert exc_info.value.code == ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID


def test_invalid_source_field_raises(tmp_path: Path) -> None:
    yml = tmp_path / "bad.yml"
    yml.write_text(
        """
version: 1
levers:
  - key: FOO
    type: env
    source: illegal_source
    restart_scope: worker
    business_phase: [TRAINING]
    sensitive: false
""",
        encoding="utf-8",
    )
    with pytest.raises(AppException) as exc_info:
        OptimizationLeversService(yaml_path=yml)
    assert exc_info.value.code == ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID


def test_duplicate_key_raises(tmp_path: Path) -> None:
    yml = tmp_path / "dup.yml"
    yml.write_text(
        """
version: 1
levers:
  - key: POSE_BACKEND
    type: algorithm_models
    source: env
    restart_scope: worker
    business_phase: [TRAINING]
    sensitive: false
  - key: POSE_BACKEND
    type: algorithm_models
    source: env
    restart_scope: worker
    business_phase: [TRAINING]
    sensitive: false
""",
        encoding="utf-8",
    )
    with pytest.raises(AppException) as exc_info:
        OptimizationLeversService(yaml_path=yml)
    assert exc_info.value.code == ErrorCode.OPTIMIZATION_LEVERS_YAML_INVALID


@pytest.mark.asyncio
async def test_list_levers_basic(valid_yaml_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POSE_BACKEND", "auto")
    monkeypatch.setenv("VENUS_TOKEN", "sk-secret-123")

    svc = OptimizationLeversService(yaml_path=valid_yaml_path)
    groups = await svc.list_levers(_fake_session())

    # 非敏感 env 键
    assert len(groups.algorithm_models) == 2
    pose_entry = next(e for e in groups.algorithm_models if e.key == "POSE_BACKEND")
    assert pose_entry.current_value == "auto"
    assert pose_entry.is_configured is None

    # 敏感键：current_value 必须为 None，is_configured=True
    venus_entry = next(e for e in groups.algorithm_models if e.key == "VENUS_TOKEN")
    assert venus_entry.current_value is None
    assert venus_entry.is_configured is True


@pytest.mark.asyncio
async def test_sensitive_key_not_configured(valid_yaml_path: Path, monkeypatch) -> None:
    # 删除 VENUS_TOKEN 环境变量
    monkeypatch.delenv("VENUS_TOKEN", raising=False)
    svc = OptimizationLeversService(yaml_path=valid_yaml_path)
    groups = await svc.list_levers(_fake_session())

    venus_entry = next(e for e in groups.algorithm_models if e.key == "VENUS_TOKEN")
    assert venus_entry.current_value is None
    assert venus_entry.is_configured is False


@pytest.mark.asyncio
async def test_phase_filter(valid_yaml_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POSE_BACKEND", "auto")
    svc = OptimizationLeversService(yaml_path=valid_yaml_path)

    # INFERENCE 阶段：kb_extraction.concurrency (TRAINING only) 应被过滤掉
    groups_inf = await svc.list_levers(_fake_session(), phase=BusinessPhase.INFERENCE)
    keys = [e.key for e in groups_inf.runtime_params]
    assert "task_channel_configs.kb_extraction.concurrency" not in keys
    # POSE_BACKEND 影响 TRAINING+INFERENCE，应保留
    keys_alg = [e.key for e in groups_inf.algorithm_models]
    assert "POSE_BACKEND" in keys_alg

    # STANDARDIZATION 阶段：仅保留含 STANDARDIZATION 的（valid_yaml 无）
    groups_std = await svc.list_levers(_fake_session(), phase=BusinessPhase.STANDARDIZATION)
    assert groups_std.runtime_params == []
    assert groups_std.algorithm_models == []


@pytest.mark.asyncio
async def test_no_sensitive_leak(valid_yaml_path: Path, monkeypatch) -> None:
    """T055：敏感键的 model_dump 必须不含 current_value 实际值."""
    monkeypatch.setenv("VENUS_TOKEN", "sk-secret-leak-check-123")
    svc = OptimizationLeversService(yaml_path=valid_yaml_path)
    groups = await svc.list_levers(_fake_session())

    venus = next(e for e in groups.algorithm_models if e.key == "VENUS_TOKEN")
    dump = venus.model_dump()
    # 敏感键 current_value 必须为 None
    assert dump["current_value"] is None
    # 泄露白盒检查：sk-secret 字符串不应出现在 dump 任何值中
    dump_str = str(dump)
    assert "sk-secret-leak-check-123" not in dump_str
