"""Feature-021 T044 — 清洗规范版本化集成测试.

验证 spec FR-005 / FR-018 + Clarifications Q1：

1. v1 规范存在 + v2 规范存在 ⇒ ``latest_version()`` 返回 ``"v2"``
2. 不显式声明 rubric_version + ``submit_curation`` ⇒ 用 latest（v2）
3. 显式声明 ``"v1"`` ⇒ 跑 v1（即使 v2 存在）
4. 同视频已有 success(v1) 作业，再提交不带 force 的 v2 ⇒ ``CURATION_RUBRIC_MISMATCH``
5. 同视频已有 success(v1) 作业 + ``force=true`` + v2 ⇒ 新建独立 job
6. 完整 schema 校验 — 加载坏的 v3 文件 ⇒ ``RUBRIC_INVALID``，旧版本不受影响

测试隔离：
- 复制现有 v1.yaml 到 tmp_path 作为基线，并合成 v2 / v3 文件，monkeypatch
  ``rubric_loader._RUBRIC_DIR`` 指向 tmp_path；测试结束后恢复
- DB 用 AsyncMock；service 层 _resolve_preprocessing_job / _fetch_classification
  / _find_existing_success_job 用 monkeypatch 注入预期返回
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.api.errors import AppException, ErrorCode
from src.services.curation import curation_service, rubric_loader


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_RUBRIC_DIR = _REPO_ROOT / "src" / "config" / "curation_rubric"
_REAL_SCHEMA = _REAL_RUBRIC_DIR / "schema.json"


@pytest.fixture
def isolated_rubric_dir(tmp_path, monkeypatch):
    """复制 v1.yaml + schema.json + prompts/ 到 tmp_path，并把 rubric_loader
    指向该临时目录；测试间隔离。
    """
    # Copy real v1 + schema + prompt template so all relative paths resolve
    shutil.copy(_REAL_RUBRIC_DIR / "v1.yaml", tmp_path / "v1.yaml")
    shutil.copy(_REAL_SCHEMA, tmp_path / "schema.json")
    (tmp_path / "prompts").mkdir()
    shutil.copy(
        _REAL_RUBRIC_DIR / "prompts" / "segment_decision_v1.md",
        tmp_path / "prompts" / "segment_decision_v1.md",
    )

    monkeypatch.setattr(rubric_loader, "_RUBRIC_DIR", tmp_path)
    monkeypatch.setattr(rubric_loader, "_SCHEMA_PATH", tmp_path / "schema.json")
    rubric_loader.reset_cache()

    yield tmp_path

    rubric_loader.reset_cache()


def _write_v2_rubric(tmp_path: Path) -> None:
    """合成一个合法的 v2.yaml — 把 low_quality_ratio 调到 0.4 模拟阈值升级。"""
    payload = yaml.safe_load((tmp_path / "v1.yaml").read_text(encoding="utf-8"))
    payload["version"] = "v2"
    payload["description"] = "test v2 — low_quality_ratio raised to 0.4"
    payload["thresholds"]["low_quality_ratio"] = 0.4
    (tmp_path / "v2.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _write_broken_rubric(tmp_path: Path, version: str) -> None:
    """合成一份 schema 校验会失败的文件（thresholds.validity_score_accept = 1.5 越界）."""
    payload = yaml.safe_load((tmp_path / "v1.yaml").read_text(encoding="utf-8"))
    payload["version"] = version
    payload["thresholds"]["validity_score_accept"] = 1.5  # 越界
    (tmp_path / f"{version}.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# 用例 1+2 — 多版本并存 + latest_version 派生
# ─────────────────────────────────────────────────────────────────────


def test_v1_and_v2_coexist_and_latest_is_v2(isolated_rubric_dir):
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    versions = rubric_loader.list_available_versions()
    assert versions == ["v1", "v2"]
    assert rubric_loader.latest_version() == "v2"

    # 加载 v1 与 v2 都通过 schema 校验
    v1 = rubric_loader.load("v1")
    v2 = rubric_loader.load("v2")
    assert v1.version == "v1"
    assert v2.version == "v2"
    assert v1.low_quality_ratio == 0.3
    assert v2.low_quality_ratio == 0.4  # 升级后


def test_load_default_picks_latest_after_v2_published(isolated_rubric_dir):
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    default = rubric_loader.load()
    assert default.version == "v2"


# ─────────────────────────────────────────────────────────────────────
# 用例 3 — 显式声明 v1 即使 v2 已发布
# ─────────────────────────────────────────────────────────────────────


def test_explicit_v1_loads_v1_even_when_v2_exists(isolated_rubric_dir):
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    r = rubric_loader.load("v1")
    assert r.version == "v1"
    assert r.low_quality_ratio == 0.3  # 老阈值


# ─────────────────────────────────────────────────────────────────────
# 用例 4+5 — submit_curation 的版本不一致路径
# ─────────────────────────────────────────────────────────────────────


def _make_session_for_submit(
    *,
    classification: SimpleNamespace,
    preprocessing_job: SimpleNamespace,
    existing_job: SimpleNamespace | None,
    channel_inflight: int = 0,
):
    """submit_curation 内部按以下顺序读 session.execute:

    1. SELECT CoachVideoClassification by id           → classification
    2. SELECT VideoPreprocessingJob by cos+success    → preprocessing_job
    3. SELECT VideoCurationJob by classification+success ORDER ⇒ existing_job
    4. SELECT inflight count from analysis_tasks       → channel_inflight
    5. INSERT job + INSERT task (no read needed)
    6. commit
    """
    session = AsyncMock()
    results: list[MagicMock] = []

    def _r_one(value):
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        return r

    def _r_count(value):
        r = MagicMock()
        r.scalar_one = MagicMock(return_value=value)
        return r

    results.append(_r_one(classification))
    results.append(_r_one(preprocessing_job))
    results.append(_r_one(existing_job))
    results.append(_r_count(channel_inflight))
    results.append(MagicMock())  # INSERTs return nothing readable

    def _execute_side_effect(*a, **kw):
        if results:
            return results.pop(0)
        return MagicMock()

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _make_classification(force_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=force_id or uuid.uuid4(),
        cos_object_key="charhuang/x/y.mp4",
        filename="y.mp4",
        tech_category="forehand_topspin",
        preprocessed=True,
    )


def _make_pp_job():
    return SimpleNamespace(id=uuid.uuid4())


def _make_existing_curation_job(rubric_version: str):
    return SimpleNamespace(
        id=uuid.uuid4(),
        cos_object_key="charhuang/x/y.mp4",
        curation_rubric_version=rubric_version,
        status="success",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_v2_rejects_when_existing_v1_without_force(
    isolated_rubric_dir, monkeypatch,
):
    """既有 success(v1) 作业，再提交 v2 + force=false ⇒ CURATION_RUBRIC_MISMATCH."""
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    cls_id = uuid.uuid4()
    cls = _make_classification(cls_id)
    pp = _make_pp_job()
    existing = _make_existing_curation_job("v1")

    # Mock channel service to avoid TaskChannelService DB query
    fake_cfg = SimpleNamespace(enabled=True, queue_capacity=20)
    monkeypatch.setattr(
        "src.services.task_channel_service.TaskChannelService.load_config",
        AsyncMock(return_value=fake_cfg),
    )

    session = _make_session_for_submit(
        classification=cls, preprocessing_job=pp, existing_job=existing,
    )

    with pytest.raises(AppException) as exc_info:
        await curation_service.submit_curation(
            session,
            classification_id=cls_id,
            rubric_version="v2",
            force=False,
        )
    assert exc_info.value.code == ErrorCode.CURATION_RUBRIC_MISMATCH
    details = exc_info.value.details or {}
    assert details["existing_rubric_version"] == "v1"
    assert details["submitted_rubric_version"] == "v2"
    # Never reached the channel / INSERT path
    session.add.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_v2_force_true_creates_new_job(
    isolated_rubric_dir, monkeypatch,
):
    """既有 success(v1) + force=true + v2 ⇒ 新建独立 job（不抛 mismatch）."""
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    cls_id = uuid.uuid4()
    cls = _make_classification(cls_id)
    pp = _make_pp_job()
    existing = _make_existing_curation_job("v1")  # 存在但 force=true 跳过短路

    fake_cfg = SimpleNamespace(enabled=True, queue_capacity=20)
    monkeypatch.setattr(
        "src.services.task_channel_service.TaskChannelService.load_config",
        AsyncMock(return_value=fake_cfg),
    )
    # Stub Celery dispatch to no-op
    monkeypatch.setattr(
        "src.workers.curation_task.curate_video.apply_async",
        MagicMock(return_value=None),
    )

    session = _make_session_for_submit(
        classification=cls, preprocessing_job=pp, existing_job=existing,
    )

    out = await curation_service.submit_curation(
        session, classification_id=cls_id,
        rubric_version="v2", force=True,
    )
    assert out.curation_rubric_version == "v2"
    assert out.queued is True
    assert out.idempotent_short_circuit is False
    # session.add called twice: VideoCurationJob + AnalysisTask
    assert session.add.call_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_v1_idempotent_short_circuits_existing_v1(
    isolated_rubric_dir, monkeypatch,
):
    """既有 success(v1) + 重新提交 v1 + force=false ⇒ 幂等短路返回老 job."""
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    cls_id = uuid.uuid4()
    cls = _make_classification(cls_id)
    pp = _make_pp_job()
    existing = _make_existing_curation_job("v1")

    fake_cfg = SimpleNamespace(enabled=True, queue_capacity=20)
    monkeypatch.setattr(
        "src.services.task_channel_service.TaskChannelService.load_config",
        AsyncMock(return_value=fake_cfg),
    )

    session = _make_session_for_submit(
        classification=cls, preprocessing_job=pp, existing_job=existing,
    )

    out = await curation_service.submit_curation(
        session, classification_id=cls_id,
        rubric_version="v1", force=False,
    )
    assert out.idempotent_short_circuit is True
    assert out.queued is False
    assert out.task_id is None
    assert out.job_id == existing.id
    assert out.curation_rubric_version == "v1"
    session.add.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_default_picks_latest_when_no_existing_job(
    isolated_rubric_dir, monkeypatch,
):
    """无既有作业 + 不传 rubric_version ⇒ 用 latest（v2）."""
    _write_v2_rubric(isolated_rubric_dir)
    rubric_loader.reset_cache()

    cls_id = uuid.uuid4()
    cls = _make_classification(cls_id)
    pp = _make_pp_job()

    fake_cfg = SimpleNamespace(enabled=True, queue_capacity=20)
    monkeypatch.setattr(
        "src.services.task_channel_service.TaskChannelService.load_config",
        AsyncMock(return_value=fake_cfg),
    )
    monkeypatch.setattr(
        "src.workers.curation_task.curate_video.apply_async",
        MagicMock(return_value=None),
    )

    session = _make_session_for_submit(
        classification=cls, preprocessing_job=pp, existing_job=None,
    )

    out = await curation_service.submit_curation(
        session, classification_id=cls_id,
        rubric_version=None, force=False,
    )
    assert out.curation_rubric_version == "v2"
    assert out.queued is True


# ─────────────────────────────────────────────────────────────────────
# 用例 6 — 损坏的 v3 不影响 v1 / v2 加载
# ─────────────────────────────────────────────────────────────────────


def test_broken_v3_does_not_affect_v1_v2(isolated_rubric_dir):
    _write_v2_rubric(isolated_rubric_dir)
    _write_broken_rubric(isolated_rubric_dir, "v3")  # schema 校验失败
    rubric_loader.reset_cache()

    # list_available_versions 只看文件名，所以 v3 仍出现
    versions = rubric_loader.list_available_versions()
    assert versions == ["v1", "v2", "v3"]

    # latest_version 也是 v3（按文件名最大）；下游 load(v3) 会报错——这是
    # spec FR-007 期望行为：上线损坏规范文件本身就要 fail-fast，让运维感知。
    with pytest.raises(AppException) as exc_info:
        rubric_loader.load("v3")
    assert exc_info.value.code == ErrorCode.RUBRIC_INVALID

    # v1 / v2 仍能正常加载，不受 v3 损坏影响
    assert rubric_loader.load("v1").version == "v1"
    assert rubric_loader.load("v2").version == "v2"
