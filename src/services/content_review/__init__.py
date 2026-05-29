"""Feature-022 — 内容审核门服务包.

模块清单：
- :mod:`review_gate`     审核门控查询（router 与 DAG 共用）
- :mod:`stale_handler`   清洗 success 后批量将 approved 条目标记为 stale
- :mod:`review_service`  审核工作台核心服务（列表 / 详情 / 决策提交 / 统计 / 开关）

详见 :doc:`specs/022-content-review-workflow/plan.md` § 4。
"""

from src.services.content_review.backlog_monitor import check_pending_backlog
from src.services.content_review.review_gate import (
    GateDecision,
    ReviewGateResult,
    evaluate_review_gate,
)
from src.services.content_review.review_service import (
    ListReviewsFilters,
    ListReviewsResult,
    get_review_detail,
    get_stats,
    list_reviews,
    record_pending_metrics,
    submit_decision,
)
from src.services.content_review.stale_handler import mark_stale_after_recurate

__all__ = [
    "check_pending_backlog",
    "GateDecision",
    "ReviewGateResult",
    "evaluate_review_gate",
    "ListReviewsFilters",
    "ListReviewsResult",
    "list_reviews",
    "get_review_detail",
    "submit_decision",
    "get_stats",
    "record_pending_metrics",
    "mark_stale_after_recurate",
]
