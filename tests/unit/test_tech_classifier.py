"""Unit tests for TechClassifier — T007 (TDD).

Tests:
  1. 关键词精确命中 → 返回正确 tech_category
  2. 精细分类优先（正手拉下旋 > 正手拉球）
  3. 多关键词 → tech_tags 填充
  4. 无关键词命中 → 调用 LLM 兜底
  5. LLM 返回有效类别 → classification_source=llm
  6. LLM 置信度 < 0.5 → tech_category=unclassified
  7. 所有 tech_category 枚举值有效性
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# These imports will fail until T008 is implemented — that's expected (TDD)
from src.services.tech_classifier import TECH_CATEGORIES, TechClassifier


RULES_FIXTURE = {
    "forehand_push_long": ["劈长"],
    "forehand_topspin_backspin": ["正手拉下旋", "正手下旋拉球"],
    "forehand_topspin": ["正手拉球", "正手上旋拉球", "正手弧圈"],
    "forehand_attack": ["正手攻球", "正手攻"],
    "serve": ["发球"],
    "receive": ["接发球"],
    "footwork": ["步法", "步伐", "移动"],
    "general": ["综合", "前言", "总结", "实战"],
}


@pytest.fixture
def classifier(tmp_path):
    """Create a TechClassifier with fixture rules (no real LLM)."""
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(RULES_FIXTURE), encoding="utf-8")
    return TechClassifier(rules_path=str(rules_path), llm_client=None)


@pytest.fixture
def classifier_with_llm(tmp_path):
    """Create a TechClassifier with fixture rules and mocked LLM."""
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps(RULES_FIXTURE), encoding="utf-8")
    mock_llm = MagicMock()
    return TechClassifier(rules_path=str(rules_path), llm_client=mock_llm)


class TestKeywordMatching:
    def test_exact_keyword_hit(self, classifier):
        result = classifier.classify("22_正手拉球解析.mp4", "某课程系列")
        assert result.tech_category == "forehand_topspin"
        assert result.classification_source == "rule"
        assert result.confidence == 1.0

    def test_fine_grained_priority(self, classifier):
        """正手拉下旋 should match forehand_topspin_backspin, not forehand_topspin."""
        result = classifier.classify("05_正手拉下旋技术详解.mp4", "某课程系列")
        assert result.tech_category == "forehand_topspin_backspin"
        assert result.classification_source == "rule"

    def test_劈长_keyword(self, classifier):
        result = classifier.classify("03_劈长技术入门.mp4", "某课程系列")
        assert result.tech_category == "forehand_push_long"

    def test_serve_keyword(self, classifier):
        result = classifier.classify("01_发球基础课.mp4", "某课程系列")
        assert result.tech_category == "serve"

    def test_footwork_keyword(self, classifier):
        result = classifier.classify("10_步法移动训练.mp4", "某课程系列")
        assert result.tech_category == "footwork"

    def test_multi_keyword_tech_tags(self, classifier):
        """文件名含多个关键词时，tech_tags 应填入额外命中的类别。"""
        result = classifier.classify("综合正手攻球练习.mp4", "某课程系列")
        # 第一个命中为主类别，其余放 tech_tags
        assert result.tech_category in TECH_CATEGORIES
        assert isinstance(result.tech_tags, list)

    def test_raw_tech_desc_extracted(self, classifier):
        """raw_tech_desc 应从文件名中提取匹配到的关键词。"""
        result = classifier.classify("08_正手拉球练习.mp4", "某课程系列")
        assert result.raw_tech_desc is not None
        assert len(result.raw_tech_desc) > 0


class TestLlmFallback:
    def test_no_keyword_triggers_llm(self, classifier_with_llm):
        """当规则未命中时，应调用 LLM。"""
        mock_llm = classifier_with_llm.llm_client
        mock_llm.chat.return_value = (
            '{"tech_category": "forehand_attack", "confidence": 0.85, "reason": "test"}',
            50,
        )
        result = classifier_with_llm.classify("右脚找位解析.mp4", "某课程系列")
        assert mock_llm.chat.called
        assert result.tech_category == "forehand_attack"
        assert result.classification_source == "llm"
        assert result.confidence == 0.85

    def test_llm_low_confidence_degrades_to_unclassified(self, classifier_with_llm):
        """LLM 置信度 < 0.5 时，应降级为 unclassified。"""
        mock_llm = classifier_with_llm.llm_client
        mock_llm.chat.return_value = (
            '{"tech_category": "footwork", "confidence": 0.3, "reason": "不确定"}',
            50,
        )
        result = classifier_with_llm.classify("神秘技术视频.mp4", "某课程系列")
        assert result.tech_category == "unclassified"
        assert result.classification_source == "llm"

    def test_no_llm_no_keyword_returns_unclassified(self, classifier):
        """无 LLM 且规则未命中时，返回 unclassified。"""
        result = classifier.classify("未知内容.mp4", "某课程系列")
        assert result.tech_category == "unclassified"
        assert result.classification_source == "rule"

    def test_llm_invalid_category_returns_unclassified(self, classifier_with_llm):
        """LLM 返回无效 tech_category 时，降级为 unclassified。"""
        mock_llm = classifier_with_llm.llm_client
        mock_llm.chat.return_value = (
            '{"tech_category": "invalid_category", "confidence": 0.9, "reason": "test"}',
            50,
        )
        result = classifier_with_llm.classify("某技术.mp4", "某课程系列")
        assert result.tech_category == "unclassified"


class TestTechCategoriesEnum:
    def test_all_required_categories_present(self):
        required = {
            "forehand_push_long",
            "forehand_attack",
            "forehand_topspin",
            "forehand_topspin_backspin",
            "forehand_loop_fast",
            "forehand_loop_high",
            "forehand_flick",
            "backhand_attack",
            "backhand_topspin",
            "backhand_topspin_backspin",
            "backhand_flick",
            "backhand_push",
            "serve",
            "receive",
            "footwork",
            "forehand_backhand_transition",
            "defense",
            "penhold_reverse",
            "stance_posture",
            "general",
            "unclassified",
        }
        assert required.issubset(set(TECH_CATEGORIES))

    def test_tech_categories_count(self):
        assert len(TECH_CATEGORIES) >= 21
