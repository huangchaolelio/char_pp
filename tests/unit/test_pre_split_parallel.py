"""T006: 单元测试 — _pre_split_video 并行化改造

测试策略：
- Mock subprocess.run 以避免真实 ffmpeg 调用
- 验证并行执行路径（ThreadPoolExecutor）
- 验证失败取消语义（任一失败 → 整体抛 RuntimeError）
- 验证 max_workers = min(4, total_segments)

注：使用 ThreadPoolExecutor（不是 ProcessPoolExecutor）因为 Celery prefork
worker 是 daemon 进程，无法 fork 子进程；ffmpeg 是 subprocess，线程池同样有效。
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(
    reason="Feature-013 retired src/workers/expert_video_task.py (Alembic 0012). "
    "Parallel pre-split logic was part of the legacy 11-step pipeline."
)

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("COS_SECRET_ID", "test-id")
os.environ.setdefault("COS_SECRET_KEY", "test-key")
os.environ.setdefault("COS_REGION", "ap-guangzhou")
os.environ.setdefault("COS_BUCKET", "test-bucket")

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_subprocess_result(returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPreSplitParallel:
    """Verify _pre_split_video parallel behavior after T008 refactor."""

    def test_all_segments_success(self, tmp_path):
        """All segments succeed → returns list of Paths, length == total_segments."""
        from src.workers.expert_video_task import _pre_split_video

        src = tmp_path / "video.mp4"
        src.touch()

        def fake_run(cmd, **kwargs):
            seg_path = Path(cmd[-1])
            seg_path.touch()
            return _make_subprocess_result(0)

        with patch("src.workers.expert_video_task.subprocess.run", side_effect=fake_run):
            result = _pre_split_video(src, segment_duration_s=30, total_segments=4)

        assert len(result) == 4
        assert all(p is not None for p in result), "All segments should succeed"

    def test_partial_failure_cancels_all(self, tmp_path):
        """Any segment failure → raises RuntimeError (fail-fast semantics, FR-007)."""
        from src.workers.expert_video_task import _pre_split_video

        src = tmp_path / "video.mp4"
        src.touch()

        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            seg_path = Path(cmd[-1])
            if call_count["n"] == 2:
                return _make_subprocess_result(1)
            seg_path.touch()
            return _make_subprocess_result(0)

        with pytest.raises(RuntimeError, match="pre.split"):
            with patch("src.workers.expert_video_task.subprocess.run", side_effect=fake_run):
                _pre_split_video(src, segment_duration_s=30, total_segments=4)

    def test_max_workers_capped_at_4(self, tmp_path):
        """ThreadPoolExecutor is constructed with max_workers=min(4, total_segments)."""
        from concurrent.futures import ThreadPoolExecutor

        from src.workers.expert_video_task import _pre_split_video

        src = tmp_path / "video.mp4"
        src.touch()

        captured_kwargs = {}
        original_tpe = ThreadPoolExecutor

        class CapturingTPE(original_tpe):
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                super().__init__(*args, **kwargs)

        def fake_run(cmd, **kwargs):
            seg_path = Path(cmd[-1])
            seg_path.touch()
            return _make_subprocess_result(0)

        with (
            patch("src.workers.expert_video_task.ThreadPoolExecutor", CapturingTPE),
            patch("src.workers.expert_video_task.subprocess.run", side_effect=fake_run),
        ):
            _pre_split_video(src, segment_duration_s=30, total_segments=6)

        assert captured_kwargs.get("max_workers") == 4, (
            f"max_workers should be min(4, 6)=4, got {captured_kwargs.get('max_workers')}"
        )

    def test_max_workers_fewer_than_4_when_segments_less(self, tmp_path):
        """When total_segments < 4, max_workers == total_segments."""
        from concurrent.futures import ThreadPoolExecutor

        from src.workers.expert_video_task import _pre_split_video

        src = tmp_path / "video.mp4"
        src.touch()

        captured_kwargs = {}
        original_tpe = ThreadPoolExecutor

        class CapturingTPE(original_tpe):
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                super().__init__(*args, **kwargs)

        def fake_run(cmd, **kwargs):
            seg_path = Path(cmd[-1])
            seg_path.touch()
            return _make_subprocess_result(0)

        with (
            patch("src.workers.expert_video_task.ThreadPoolExecutor", CapturingTPE),
            patch("src.workers.expert_video_task.subprocess.run", side_effect=fake_run),
        ):
            _pre_split_video(src, segment_duration_s=30, total_segments=2)

        assert captured_kwargs.get("max_workers") == 2, (
            f"max_workers should be min(4, 2)=2, got {captured_kwargs.get('max_workers')}"
        )

    def test_single_segment_uses_no_pool(self, tmp_path):
        """With 1 segment, should still work (edge case)."""
        from src.workers.expert_video_task import _pre_split_video

        src = tmp_path / "video.mp4"
        src.touch()

        def fake_run(cmd, **kwargs):
            seg_path = Path(cmd[-1])
            seg_path.touch()
            return _make_subprocess_result(0)

        with patch("src.workers.expert_video_task.subprocess.run", side_effect=fake_run):
            result = _pre_split_video(src, segment_duration_s=30, total_segments=1)

        assert len(result) == 1
        assert result[0] is not None
