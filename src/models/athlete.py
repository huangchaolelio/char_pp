"""Athlete ORM model — Feature-020 运动员实体（与 Coach 结构对称）.

由 ``CosAthleteScanner._upsert_athlete()`` 在扫描运动员 COS 根路径时自动同步。
本 feature **不提供**运动员 CRUD 入口；数据来源仅来自扫描器。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class Athlete(Base):
    __tablename__ = "athletes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 固定 'athlete_scan'，预留未来手工入口；枚举由 CHECK 约束在迁移中维护
    created_via: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'athlete_scan'"), default="athlete_scan"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
        onupdate=text("timezone('Asia/Shanghai', now())"),
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_athletes_name"),
    )
