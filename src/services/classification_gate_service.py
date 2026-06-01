"""ClassificationGateService — pre-check that a coach video has been classified.

Feature-013 US1/FR-004a: before enqueueing a ``kb_extraction`` task for a COS
object, we require that ``coach_video_classifications.action`` is set to a
real dictionary action (not NULL, not 'unclassified').

Feature-023: 门槛判定从 ``tech_category != 'unclassified'`` 改为
``action IS NOT NULL AND action != 'unclassified'``（章程 X 单 active 约束作用域
变更：per-tech_category → per-action）.

Feature-023 / Path 1' （拓展字典 44→56）：新增 「通用·教学辅助」 与 「·步法」
L3 桥接 1015 表面 1015 视频中的「握拍/站位/前言/总结/结梯训练/接发球/步法」类
辅助视频。这些 L3 不包含可提取的技术要点结构，必须在 KB 提取门控运行期额外
过滤。过滤集合定义为 `_KB_EXCLUDED_L3`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.coach_video_classification import CoachVideoClassification


class ClassificationGateService:
    """Guard kb_extraction submissions against unclassified videos."""

    UNCLASSIFIED = "unclassified"

    # Feature-023 / Path 1' — 以下 L3 是辅助类目，不包含技术动作结构，不进入 KB 提取。
    # 这些 L3 覆盖 1015 实际视频中的「握拍/站位/前言/总结/结梯训练/接发球/跨位步法」类。
    _KB_EXCLUDED_L3: frozenset[str] = frozenset({
        "通用·教学辅助",
        "正手·步法",
        "反手·步法",
    })

    async def check_classified(
        self, session: AsyncSession, cos_object_key: str
    ) -> bool:
        """Return True if the COS object has a real (non-unclassified) action
        AND its category_l3 is NOT in the auxiliary KB-excluded set.

        Returns False when:
          - There is no ``coach_video_classifications`` row for this key.
          - The row's ``action`` is NULL or 'unclassified'.
          - The row's ``category_l3`` belongs to ``_KB_EXCLUDED_L3``
            (如 「通用·教学辅助」/「·步法」，不具备提取价值).
        """
        row = (
            await session.execute(
                select(
                    CoachVideoClassification.action,
                    CoachVideoClassification.category_l3,
                ).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).first()
        if row is None:
            return False
        action, category_l3 = row
        if not action or action == self.UNCLASSIFIED:
            return False
        if category_l3 in self._KB_EXCLUDED_L3:
            return False
        return True

    async def get_action(
        self, session: AsyncSession, cos_object_key: str
    ) -> str | None:
        """Return the action (or None). Helper for richer error messages."""
        row = (
            await session.execute(
                select(CoachVideoClassification.action).where(
                    CoachVideoClassification.cos_object_key == cos_object_key
                )
            )
        ).scalar_one_or_none()
        return row

    # Feature-023 向后兼容别名：旧调用方 get_tech_category() → get_action()
    # 注意：此别名仅用于减少调用方一次性改造负担，长期应使用 get_action.
    async def get_tech_category(
        self, session: AsyncSession, cos_object_key: str
    ) -> str | None:  # pragma: no cover - thin alias
        return await self.get_action(session, cos_object_key)
