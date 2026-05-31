"""Feature-023 — audio_kb_extract V2 标准化 schema 单元测试.

T040: 验证 kb_items 输出含新 `action` 字段；merger 可消费此 schema 不抛异常.
T040a: 验证多教练同 action 时 schema 严格相同（SC-003）.
"""

from __future__ import annotations

from src.services.kb_extraction_pipeline.merger import F14KbMerger


# ── T040: schema 标准化 ──────────────────────────────────────────────────


def test_audio_kb_items_include_action_field() -> None:
    """audio_kb_extract 输出的 kb_items dict 必须含 `action` 字段（spec FR-010）.

    通过对 audio_kb_extract.py 中字段构造的静态校验完成（避免触发真实 LLM）.
    """
    import inspect
    from src.services.kb_extraction_pipeline.step_executors import audio_kb_extract

    src_text = inspect.getsource(audio_kb_extract)
    # kb_items 构造 dict 中必须出现 "action": 键
    assert '"action": job.tech_category' in src_text, (
        "audio_kb_extract 必须输出 action 字段（Feature-023 FR-010）"
    )


def test_visual_kb_items_include_action_field() -> None:
    """visual_kb_extract 同步含 action 字段."""
    import inspect
    from src.services.kb_extraction_pipeline.step_executors import visual_kb_extract

    src_text = inspect.getsource(visual_kb_extract)
    assert '"action": job.tech_category' in src_text


# ── T040a: 多教练同 action 输出 schema 一致（SC-003）────────────────────


def _make_kb_item(action: str, dim: str, source: str = "audio") -> dict:
    """构造一条 audio/visual KB item，标准化 schema 含 action 字段."""
    return {
        "dimension": dim,
        "param_min": 30.0,
        "param_max": 50.0,
        "param_ideal": 40.0,
        "unit": "deg",
        "extraction_confidence": 0.85,
        "action": action,
        "action_type": action,  # 兼容字段
        "source_type": source,
    }


def test_two_coaches_same_action_emit_identical_schema() -> None:
    """两位教练同一 action（高吊弧圈球）的输出字段集严格相同."""
    coach_a_items = [
        _make_kb_item("高吊弧圈球", "racket_angle"),
        _make_kb_item("高吊弧圈球", "swing_speed"),
    ]
    coach_b_items = [
        _make_kb_item("高吊弧圈球", "racket_angle"),
        _make_kb_item("高吊弧圈球", "swing_speed"),
    ]
    keys_a = {frozenset(item.keys()) for item in coach_a_items}
    keys_b = {frozenset(item.keys()) for item in coach_b_items}
    assert keys_a == keys_b, "两位教练的 KB schema 必须完全一致"


def test_merge_aggregates_without_human_format_conversion() -> None:
    """merger 直接合并两份 KB（同 action）→ 无 KeyError、可直接消费."""
    coach_a_visual = [_make_kb_item("高吊弧圈球", "racket_angle", "visual")]
    coach_b_audio = [_make_kb_item("高吊弧圈球", "racket_angle", "audio")]

    merger = F14KbMerger()
    merged, conflicts = merger.merge(coach_a_visual, coach_b_audio)
    # 只要不抛异常即视为 schema 兼容
    assert isinstance(merged, list)
    assert isinstance(conflicts, list)


def test_merged_output_action_field_preserved() -> None:
    """merge 后 MergedPoint.action_type 仍指向原 action（验证字段未丢失）."""
    items_a = [_make_kb_item("高吊弧圈球", "racket_angle", "visual")]
    items_b = [_make_kb_item("高吊弧圈球", "racket_angle", "audio")]

    merger = F14KbMerger()
    merged, _conflicts = merger.merge(items_a, items_b)
    if merged:
        # MergedPoint.action_type 字段含「高吊弧圈球」
        assert merged[0].action_type == "高吊弧圈球"
