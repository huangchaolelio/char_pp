"""tests/conftest.py — 顶层 pytest 配置.

Feature-019 新增: 在任何 unit/conftest.py 的 ``os.environ.setdefault(...)`` 触发之前，
显式从项目根 ``.env`` 加载 ``DATABASE_URL`` / ``REDIS_URL`` 等关键环境变量，
避免 unit 测试的 fake URL（``postgresql+asyncpg://test:test@...``）污染后续
contract/integration 测试的数据库连接。

此文件被 pytest 默认发现并优先于子目录 conftest.py 执行（collect 顶层）。
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env at repo root; do not override existing env."""
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # 尊重已存在值（不覆盖）
        os.environ.setdefault(key, value)


_load_dotenv()
