"""TaskChannelConfig ORM model — per-task-type queue capacity & concurrency (Feature 013).

One row per ``TaskType`` value. Edited via admin API to dynamically tune
throughput without restarting workers; TaskChannelService caches each row
for ``settings.channel_config_cache_ttl_s`` seconds (default 30s) so
changes propagate within one TTL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Enum, Integer, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy import text

from src.db.session import Base
from src.models.analysis_task import TaskType


class TaskChannelConfig(Base):
    """Configuration row for a single task channel (= task_type)."""

    __tablename__ = "task_channel_configs"
    __table_args__ = (
        CheckConstraint("queue_capacity > 0", name="ck_task_channel_capacity_positive"),
        CheckConstraint("concurrency > 0", name="ck_task_channel_concurrency_positive"),
    )

    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, name="task_type_enum"),
        primary_key=True,
    )
    queue_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )
