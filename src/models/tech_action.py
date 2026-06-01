"""Feature-023 — TechAction dictionary table ORM model.

权威参考: specs/023-tech-classification-rebuild/data-model.md § 2

复合主键 (category_l1, category_l2, category_l3, action) 而非单列 action 主键，
原因：CSV 字典中存在跨手部重名 action（如「高吊弧圈球」既是正手进攻也是反手进攻）。
distinct action 数 = 35；distinct (l1,l2,l3,action) 数 = 56（v2 字典 Path 1' 拓展后）。

Seed 来源: pp_book/pp_tech_classification.csv （TSV 5 列）→ 迁移 0022 内嵌清洗
（strip U+200B 零宽字符 + 用 `·` 拼接 hand+tech_class）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, PrimaryKeyConstraint, String, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.session import Base


class TechAction(Base):
    """56 行字典表，作为所有业务表 `action` 列的复合外键目标."""

    __tablename__ = "tech_actions"

    # 复合 PK 4 列
    category_l1: Mapped[str] = mapped_column(String(32), nullable=False)
    category_l2: Mapped[str] = mapped_column(String(32), nullable=False)
    category_l3: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("timezone('Asia/Shanghai', now())"),
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "category_l1",
            "category_l2",
            "category_l3",
            "action",
            name="pk_tech_actions",
        ),
        # 退化索引：支持按层级前缀筛选 action 列表（PK 本身已含此前缀，但显式声明便于查询计划）
        Index(
            "ix_tech_actions_l1l2l3",
            "category_l1",
            "category_l2",
            "category_l3",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<TechAction l1={self.category_l1!r} l2={self.category_l2!r} "
            f"l3={self.category_l3!r} action={self.action!r}>"
        )
