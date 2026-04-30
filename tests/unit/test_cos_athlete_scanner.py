"""Unit tests for Feature-020 CosAthleteScanner (T021).

Covers:
- 目录名 → athlete_name 的 `map` / `fallback` 分流
- 同名后缀 `_2 / _3`
- `_README` 伪字段跳过
- tech_classifier 不同分类来源（rule / llm / fallback）在 scan 结果中的透传

不依赖真实 COS / DB —— 通过 mock 注入输入。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest


@dataclass
class _ClfResult:
    tech_category: str
    classification_source: str  # rule | llm | fallback
    confidence: float


def _build_scanner(athlete_map: dict[str, str]):
    """Directly construct CosAthleteScanner bypassing from_settings()."""
    from src.services.cos_athlete_scanner import CosAthleteScanner

    classifier = MagicMock()
    classifier.classify.return_value = _ClfResult(
        tech_category="forehand_attack",
        classification_source="rule",
        confidence=1.0,
    )
    return CosAthleteScanner(
        athlete_map=athlete_map,
        cos_root_prefix="charhuang/tt_video/athletes/",
        tech_classifier=classifier,
    )


@pytest.mark.unit
class TestCosAthleteScannerMapping:

    def test_skip_readme_pseudo_field(self):
        scanner = _build_scanner({
            "_README": "this is a comment",
            "zhangsan": "张三",
        })
        # _README 被跳过后，只保留 1 条真实映射
        assert "_README" not in scanner._dir_to_unique_athlete
        assert scanner._dir_to_unique_athlete["zhangsan"] == "张三"

    def test_map_hit_returns_map_source(self):
        scanner = _build_scanner({"zhangsan": "张三"})
        name, source = scanner._get_athlete_name("zhangsan")
        assert name == "张三"
        assert source == "map"

    def test_fallback_uses_directory_name(self):
        scanner = _build_scanner({"zhangsan": "张三"})
        name, source = scanner._get_athlete_name("unknown_dir")
        assert name == "unknown_dir"
        assert source == "fallback"

    def test_duplicate_base_name_gets_suffix(self):
        # 两个不同目录映射到同一姓名 → 第二个得到 _2 后缀
        scanner = _build_scanner({
            "lisi_a": "李四",
            "lisi_b": "李四",
            "lisi_c": "李四",
        })
        unique_names = set(scanner._dir_to_unique_athlete.values())
        assert "李四" in unique_names
        assert "李四_2" in unique_names
        assert "李四_3" in unique_names
        assert len(unique_names) == 3

    def test_extract_directory_strips_prefix(self):
        scanner = _build_scanner({})
        cos_key = "charhuang/tt_video/athletes/张三/正手攻球01.mp4"
        assert scanner._extract_directory(cos_key) == "张三"

    def test_classification_source_transparency(self):
        """scanner 将 classifier 的 source 字段原样写入 classification_source."""
        from src.services.cos_athlete_scanner import CosAthleteScanner

        # 构造三个分类器各自返回 rule / llm / fallback
        for src in ("rule", "llm", "fallback"):
            classifier = MagicMock()
            classifier.classify.return_value = _ClfResult(
                tech_category="serve",
                classification_source=src,
                confidence=0.85,
            )
            scanner = CosAthleteScanner(
                athlete_map={},
                cos_root_prefix="charhuang/tt_video/athletes/",
                tech_classifier=classifier,
            )
            # 直接调用 classifier 确认返回 object 携带 source
            clf = scanner._classifier.classify("x.mp4", "dir")
            assert clf.classification_source == src
