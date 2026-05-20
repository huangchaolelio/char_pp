"""Feature-021 内容清洗子包。

模块组织（参见 specs/021-video-content-curation/plan.md）：

- ``rubric_loader``         — 加载 + jsonschema 校验 ``src/config/curation_rubric/vN.yaml``
- ``decision_engine``       — 规则路 + LLM 兜底两层装配
- ``segment_text_provider`` — 从预处理 transcript 切分段文本
- ``coach_dominance_detector`` — 启发式判定目标教练主导率
- ``curation_service``      — 编排：load → decide → aggregate → persist
- ``error_codes``           — 私域错误常量映射
"""
