"""Feature-017 阶段 6 T067：INTERNAL_ERROR 兜底 handler 合约测试.

验证章程 v1.4.0 原则 IX：
  "未预期异常 → 500 + INTERNAL_ERROR（含 logging.exception，不泄露栈）"

手段：在沙盒 FastAPI app 上挂一条强制抛 RuntimeError 的路由，然后断言：
  1. HTTP 状态 500
  2. 响应体为错误信封（success=False + error.code=INTERNAL_ERROR）
  3. 响应体 error.details 为 None（不泄露栈/业务细节）
  4. logging.exception 日志含 traceback（用 caplog fixture 断言）
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.errors import register_exception_handlers


@pytest.fixture()
def sandbox_app() -> FastAPI:
    """独立沙盒 app，挂一条强制抛异常的路由用于测试兜底."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def _boom() -> dict:
        raise RuntimeError("synthetic explosion for internal-error test")

    return app


class TestInternalErrorHandler:

    def test_500_internal_error_envelope(
        self, sandbox_app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        """未预期异常统一转为 500 + INTERNAL_ERROR + 不泄露 details."""
        client = TestClient(sandbox_app, raise_server_exceptions=False)

        with caplog.at_level(logging.ERROR, logger="src.api.errors"):
            resp = client.get("/boom")

        assert resp.status_code == 500
        body = resp.json()

        # 信封结构严格
        assert body["success"] is False
        assert body["error"]["code"] == "INTERNAL_ERROR"
        # details 必须为 None（不泄露栈 / 业务细节）
        assert body["error"]["details"] is None
        # data / meta 字段不应出现在错误信封
        assert "data" not in body
        assert "meta" not in body

        # logger.exception 应打印含 traceback 的 ERROR 级日志
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "Unhandled exception" in r.getMessage() for r in error_records
        ), "缺少 'Unhandled exception' 日志（logger.exception 未触发）"
        # traceback 的 exc_info 字段应被捕获
        assert any(r.exc_info is not None for r in error_records), (
            "日志记录未包含 exc_info=True（traceback 未随 logging.exception 输出）"
        )
