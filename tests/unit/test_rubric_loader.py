"""Feature-021 T024 — 清洗规范加载器单测.

覆盖：
1. v1.yaml 通过 schema 校验
2. lru_cache 重复 load(同 version) 命中缓存
3. list_available_versions / latest_version 行为
4. 不存在版本 → RUBRIC_VERSION_NOT_FOUND
5. 损坏 YAML / schema 校验失败 → RUBRIC_INVALID
6. version 字段与文件名不匹配 → RUBRIC_INVALID
7. **CI 护栏**：``src/config/curation_rubric/v*.yaml`` 全部必须通过 schema 校验
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from src.api.errors import AppException, ErrorCode
from src.services.curation import rubric_loader


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUBRIC_DIR = _REPO_ROOT / "src" / "config" / "curation_rubric"


@pytest.fixture(autouse=True)
def _clear_rubric_cache():
    """每条用例前后清空 lru_cache，避免测试间污染。"""
    rubric_loader.reset_cache()
    yield
    rubric_loader.reset_cache()


# ── 基础加载 ─────────────────────────────────────────────────────────


def test_load_v1_passes_schema() -> None:
    r = rubric_loader.load("v1")
    assert r.version == "v1"
    assert r.threshold_accept == 0.7
    assert r.threshold_reject == 0.3
    assert r.low_quality_ratio == 0.3
    assert r.llm_invoke_band == (0.3, 0.7)
    assert r.llm_unavailable_decision == "uncertain"


def test_load_caches_result() -> None:
    r1 = rubric_loader.load("v1")
    r2 = rubric_loader.load("v1")
    assert r1 is r2  # lru_cache hit


def test_list_versions_and_latest() -> None:
    versions = rubric_loader.list_available_versions()
    assert "v1" in versions
    # 顺序应递增
    nums = [int(re.match(r"v(\d+)", v).group(1)) for v in versions]
    assert nums == sorted(nums)
    latest = rubric_loader.latest_version()
    assert latest == versions[-1]


def test_load_default_uses_latest() -> None:
    """load() 不传 version 时取 latest。"""
    r_default = rubric_loader.load()
    r_latest = rubric_loader.load(rubric_loader.latest_version())
    assert r_default.version == r_latest.version


# ── 错误路径 ─────────────────────────────────────────────────────────


def test_unknown_version_raises_not_found() -> None:
    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v999")
    assert exc_info.value.code == ErrorCode.RUBRIC_VERSION_NOT_FOUND


def test_invalid_version_literal_raises_invalid() -> None:
    """非 ``vN`` 格式的 version 字符串视为非法。"""
    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v1.0")
    assert exc_info.value.code == ErrorCode.RUBRIC_INVALID


def test_corrupted_yaml_raises_invalid(tmp_path, monkeypatch) -> None:
    """临时把 _RUBRIC_DIR 指到 tmp_path，写入损坏 YAML，断言 RUBRIC_INVALID。"""
    bad = tmp_path / "v1.yaml"
    bad.write_text("version: v1\nrules: [unclosed", encoding="utf-8")
    monkeypatch.setattr(rubric_loader, "_RUBRIC_DIR", tmp_path)
    monkeypatch.setattr(rubric_loader, "_SCHEMA_PATH", _RUBRIC_DIR / "schema.json")
    rubric_loader.reset_cache()

    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v1")
    assert exc_info.value.code == ErrorCode.RUBRIC_INVALID


def test_version_filename_mismatch_raises_invalid(tmp_path, monkeypatch) -> None:
    """文件名 ``v9.yaml`` 但顶层 ``version: v1`` ⇒ RUBRIC_INVALID。"""
    fake = tmp_path / "v9.yaml"
    payload = yaml.safe_load((_RUBRIC_DIR / "v1.yaml").read_text(encoding="utf-8"))
    fake.write_text(yaml.safe_dump(payload), encoding="utf-8")  # version 仍是 v1
    monkeypatch.setattr(rubric_loader, "_RUBRIC_DIR", tmp_path)
    monkeypatch.setattr(rubric_loader, "_SCHEMA_PATH", _RUBRIC_DIR / "schema.json")
    rubric_loader.reset_cache()

    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v9")
    assert exc_info.value.code == ErrorCode.RUBRIC_INVALID
    assert "version_filename_mismatch" in exc_info.value.message


def test_schema_violation_raises_invalid(tmp_path, monkeypatch) -> None:
    """阈值越界（validity_score_accept = 1.5）→ RUBRIC_INVALID。"""
    payload = yaml.safe_load((_RUBRIC_DIR / "v1.yaml").read_text(encoding="utf-8"))
    payload["thresholds"]["validity_score_accept"] = 1.5  # 越界
    bad = tmp_path / "v1.yaml"
    bad.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setattr(rubric_loader, "_RUBRIC_DIR", tmp_path)
    monkeypatch.setattr(rubric_loader, "_SCHEMA_PATH", _RUBRIC_DIR / "schema.json")
    rubric_loader.reset_cache()

    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v1")
    assert exc_info.value.code == ErrorCode.RUBRIC_INVALID
    assert "schema_errors" in (exc_info.value.details or {})


# ── CI 护栏：所有 vN.yaml 必须能加载 ───────────────────────────────


def test_all_published_versions_load_cleanly() -> None:
    """src/config/curation_rubric/v*.yaml 全部必须通过 schema 校验.

    本测试是上线前的 CI 护栏：新增 vN.yaml 而不通过本测试 ⇒ CI 阻断；
    避免"线上 fail-fast 才发现规范文件错"的事故。
    """
    versions = rubric_loader.list_available_versions()
    assert versions, "没有任何 vN.yaml；至少应有 v1.yaml"
    for v in versions:
        r = rubric_loader.load(v)
        assert r.version == v
