"""Feature-019 teaching_tip service — KB approve 联动批量归档/激活.

对齐:
  - spec.md FR-022 / FR-024
  - data-model.md § 实体 3 生命周期联动（2 步 UPDATE）
  - contracts/kb-version-approve.yaml::TipsUpdatedStats

设计决议（data-model.md R5）:
  - KB approve 时调用 ``relink_on_kb_approve`` 联动：
      1. 归档旧 active KB 对应的 auto tips（source_type='auto'）
      2. 激活新 KB 对应的所有 tips（auto + human）
  - source_type='human' 的 tip **不参与批量归档**，保留 Feature-005 "人工标注不可被自动流覆盖"
    语义（FR-024）。但激活阶段一视同仁（若 human tip 挂在新 KB 上即激活）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.teaching_tip import TeachingTip, TipStatus

logger = logging.getLogger(__name__)


async def relink_on_kb_approve(
    session: AsyncSession,
    *,
    tech_category: str,
    old_version: int | None,
    new_version: int,
) -> dict:
    """KB approve 事务内联动 teaching_tips 状态迁移.

    返回 ``{"archived_count": N, "activated_count": M}`` 便于路由层回报给客户端。
    所有 UPDATE 与 approve 主事务共享 session（不另开事务），由调用方提交。
    """
    # Step 1: 归档旧 active KB 下的 auto tips（human 不动，保留 FR-024）
    archived_count = 0
    if old_version is not None:
        result = await session.execute(
            update(TeachingTip)
            .where(
                TeachingTip.kb_tech_category == tech_category,
                TeachingTip.kb_version == old_version,
                TeachingTip.source_type == "auto",
                TeachingTip.status == TipStatus.active,
            )
            .values(status=TipStatus.archived)
            .execution_options(synchronize_session=False)
        )
        archived_count = int(result.rowcount or 0)

    # Step 2: 激活新 KB 下的 tips（auto + human 一视同仁）
    result = await session.execute(
        update(TeachingTip)
        .where(
            TeachingTip.kb_tech_category == tech_category,
            TeachingTip.kb_version == new_version,
            TeachingTip.status == TipStatus.draft,
        )
        .values(status=TipStatus.active)
        .execution_options(synchronize_session=False)
    )
    activated_count = int(result.rowcount or 0)

    logger.info(
        "Teaching tips relink on KB approve (tech_category=%s, old=%s, new=%d): "
        "archived=%d, activated=%d",
        tech_category,
        old_version,
        new_version,
        archived_count,
        activated_count,
    )
    return {"archived_count": archived_count, "activated_count": activated_count}


async def list_tips_by_category(
    session: AsyncSession,
    *,
    tech_category: str,
    include_statuses: list[str] | None = None,
) -> list[TeachingTip]:
    """按 tech_category 查 tips；默认仅返回 status='active'（FR-023）。

    ``include_statuses`` 扩展到 {'draft', 'archived'} 子集即放宽过滤（诊断侧默认不传）。
    """
    statuses = [TipStatus.active]
    if include_statuses:
        allowed = {"draft", "archived", "active"}
        for s in include_statuses:
            if s not in allowed:
                continue
            st = TipStatus(s)
            if st not in statuses:
                statuses.append(st)

    result = await session.execute(
        select(TeachingTip)
        .where(
            TeachingTip.tech_category == tech_category,
            TeachingTip.status.in_(statuses),
        )
        .order_by(
            TeachingTip.source_type.desc(),   # human 先 > auto 后
            TeachingTip.confidence.desc(),
        )
    )
    return list(result.scalars().all())
