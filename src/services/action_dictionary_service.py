"""Feature-023 — Action 字典服务（单点事实来源）.

权威参考: specs/023-tech-classification-rebuild/plan.md § 1.1.1 + research.md § 2

本服务是 tech_actions 字典的唯一读取入口；所有业务逻辑校验
（task_kwargs.action ∈ 字典 / LLM 输出 ∈ 字典 / KB 提交 action ∈ 字典）必须经此处。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import AsyncSessionFactory
from src.models.tech_action import TechAction


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionEntry:
    """tech_actions 字典的不可变值对象（避免 ORM 实例跨 session 泄漏）."""

    category_l1: str
    category_l2: str
    category_l3: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "category_l1": self.category_l1,
            "category_l2": self.category_l2,
            "category_l3": self.category_l3,
            "action": self.action,
        }


class ActionDictionaryService:
    """加载 tech_actions 字典并提供 (l1, l2, l3, action) 校验 + LLM prompt 块生成."""

    def __init__(self, session_factory=AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._cache: list[ActionEntry] | None = None
        self._action_index: dict[tuple[str, str, str, str], ActionEntry] | None = None
        # action 字段单列索引（重名 action 取所有候选）
        self._by_action: dict[str, list[ActionEntry]] | None = None

    # ────────────────────────────────────────────────────────────────
    # 加载与缓存
    # ────────────────────────────────────────────────────────────────

    async def load_all(self, *, force: bool = False) -> list[ActionEntry]:
        """加载全部字典；进程内缓存（force=True 时强制刷新）."""
        if self._cache is not None and not force:
            return self._cache

        async with self._session_factory() as session:
            entries = await self._fetch_all(session)
        self._cache = entries
        self._action_index = {
            (e.category_l1, e.category_l2, e.category_l3, e.action): e for e in entries
        }
        self._by_action = {}
        for e in entries:
            self._by_action.setdefault(e.action, []).append(e)

        logger.info(
            "ActionDictionaryService loaded %d entries (%d distinct actions)",
            len(entries),
            len(self._by_action),
        )
        return entries

    @staticmethod
    async def _fetch_all(session: AsyncSession) -> list[ActionEntry]:
        rows = (
            await session.execute(
                select(TechAction).order_by(
                    TechAction.category_l1,
                    TechAction.category_l2,
                    TechAction.category_l3,
                    TechAction.action,
                )
            )
        ).scalars().all()
        return [
            ActionEntry(
                category_l1=r.category_l1,
                category_l2=r.category_l2,
                category_l3=r.category_l3,
                action=r.action,
            )
            for r in rows
        ]

    # ────────────────────────────────────────────────────────────────
    # 查询与校验
    # ────────────────────────────────────────────────────────────────

    async def lookup(self, action: str) -> ActionEntry | None:
        """按单列 action 查询；存在重名时返回 **None**（调用方需提供四元组方能解歧义）.

        重名 action（如「高吊弧圈球」既是正手进攻也是反手进攻）通过
        :meth:`lookup_candidates` 取所有候选；本方法仅在唯一命中时返回。
        """
        await self.load_all()
        candidates = (self._by_action or {}).get(action, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    async def lookup_candidates(self, action: str) -> list[ActionEntry]:
        """按 action 名取所有可能的字典项（处理跨手部重名）."""
        await self.load_all()
        return list((self._by_action or {}).get(action, []))

    async def validate(
        self,
        category_l1: str,
        category_l2: str,
        category_l3: str,
        action: str,
    ) -> bool:
        """严格校验四元组是否在字典内."""
        await self.load_all()
        key = (category_l1, category_l2, category_l3, action)
        return key in (self._action_index or {})

    async def find_by_quad(
        self,
        category_l1: str,
        category_l2: str,
        category_l3: str,
        action: str,
    ) -> ActionEntry | None:
        await self.load_all()
        key = (category_l1, category_l2, category_l3, action)
        return (self._action_index or {}).get(key)

    # ────────────────────────────────────────────────────────────────
    # LLM prompt enum 块生成
    # ────────────────────────────────────────────────────────────────

    async def get_prompt_enum_block(self) -> str:
        """生成 LLM prompt 中嵌入的 56 行字典 enum 块（research § 2）.

        格式（每行一项，便于 LLM 准确解析）::

            - category_l1=横拍 | category_l2=反胶 | category_l3=正手·进攻 | action=高吊弧圈球
            ...

        Returns:
            多行字符串；总长约 600 token，可直接拼接进 system prompt.
        """
        entries = await self.load_all()
        lines = [
            f"- category_l1={e.category_l1} | category_l2={e.category_l2} "
            f"| category_l3={e.category_l3} | action={e.action}"
            for e in entries
        ]
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────────
    # 工具方法
    # ────────────────────────────────────────────────────────────────

    async def all_actions(self) -> set[str]:
        """所有 distinct action 名集合（v2 字典 56 行 → 35 个 distinct action）."""
        await self.load_all()
        return set((self._by_action or {}).keys())

    async def all_quads(self) -> set[tuple[str, str, str, str]]:
        """所有四元组集合（v2 字典 56 行）."""
        await self.load_all()
        return set((self._action_index or {}).keys())


# ── 单例（按 process 缓存；如需测试 mock 可直接 new ActionDictionaryService(...)）──
_default_service: ActionDictionaryService | None = None


def get_action_dictionary_service() -> ActionDictionaryService:
    """获取进程内默认单例（按 lazy + module-global 缓存）."""
    global _default_service
    if _default_service is None:
        _default_service = ActionDictionaryService()
    return _default_service


__all__ = [
    "ActionEntry",
    "ActionDictionaryService",
    "get_action_dictionary_service",
]
