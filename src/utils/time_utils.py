"""统一时间工具（北京时间，naive）。

全项目禁止再直接使用 ``datetime.now(timezone.utc)`` 或 ``datetime.utcnow``；
所有时间写入、比较、日志打点均通过 :func:`now_cst` 获取当前北京时间。

- 数据库列类型统一为 ``TIMESTAMP(timezone=False)`` — 存"一串数字"，按北京时间解释
- API 序列化输出形如 ``2026-04-28T11:24:16.721283``（无时区后缀）
- PostgreSQL 服务器 timezone 已配置为 ``Asia/Shanghai``，``server_default=func.now()`` 与应用层一致
"""

from datetime import datetime
from zoneinfo import ZoneInfo

__all__ = ["CST", "now_cst"]

CST = ZoneInfo("Asia/Shanghai")


def now_cst() -> datetime:
    """返回当前北京时间（naive datetime，无 tzinfo）。"""
    return datetime.now(CST).replace(tzinfo=None)
