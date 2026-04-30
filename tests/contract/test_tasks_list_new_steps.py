"""Feature-020 · T048 · GET /api/v1/tasks business_step 白名单合约测试.

覆盖 `_VALID_BUSINESS_STEPS` 白名单对 Feature-020 新增 2 个步骤的包容：
  - business_step=scan_athlete_videos     ✓ 200
  - business_step=preprocess_athlete_video ✓ 200
  - business_step=diagnose_athlete         ✓ 200（F-018 既有）

以及非法值必须回 400 INVALID_ENUM_VALUE：
  - business_step=invalid_step             ✗ 400
  - business_step=scan_anything            ✗ 400

使用 FastAPI TestClient + mock DB，不依赖真实任务数据；
路由进入 SQL 前就会在参数校验阶段完成 enum 判定。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.session import get_db
from tests.contract.conftest import assert_error_envelope, assert_success_envelope


@pytest.fixture
def mock_db():
    """Mock DB 返回空任务列表——任何查询返回 total=0 / items=[]."""
    session = AsyncMock()

    async def _exec(stmt):
        result = MagicMock()
        # total count
        result.scalar_one.return_value = 0
        # list rows
        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = _exec

    async def _fake_db():
        yield session

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(mock_db):
    return TestClient(app)


class TestBusinessStepWhitelist:

    @pytest.mark.parametrize(
        "step",
        [
            "scan_athlete_videos",
            "preprocess_athlete_video",
            "diagnose_athlete",
            "scan_cos_videos",
            "preprocess_video",
            "classify_video",
            "extract_kb",
        ],
    )
    def test_valid_steps_accepted(self, client, step):
        resp = client.get(f"/api/v1/tasks?business_step={step}")
        assert resp.status_code == 200, resp.text
        data = assert_success_envelope(resp.json())
        # 允许空列表；关键是校验放行、SQL 成功执行
        assert isinstance(data, list)

    @pytest.mark.parametrize(
        "invalid_step",
        [
            "invalid_step",
            "scan_anything",
            "athlete_scan",  # 近似但非白名单成员
            "preprocess",    # 前缀但非完整匹配
        ],
    )
    def test_invalid_step_rejected(self, client, invalid_step):
        resp = client.get(f"/api/v1/tasks?business_step={invalid_step}")
        assert resp.status_code == 400, resp.text
        err = assert_error_envelope(resp.json(), code="INVALID_ENUM_VALUE")
        # details 应含 field 名与合法枚举值列表
        assert err["details"]["field"] == "business_step"
        assert "allowed" in err["details"]
        assert "scan_athlete_videos" in err["details"]["allowed"]
        assert "preprocess_athlete_video" in err["details"]["allowed"]
