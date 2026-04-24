"""T007: 单元测试 — _pre_split_video ffmpeg 参数验证

测试策略：
- 直接测试 _split_one_segment（worker 函数）构建的 ffmpeg 命令参数
- 验证 -preset ultrafast 出现（FR-002）
- 验证 -crf 23 仍在命令中（质量保持）
- 验证 -an 仍在命令中（去掉音频）
- 验证 -vf scale=1280:720 在编码模式中存在
- 验证原来缓慢的 medium preset 不再出现
- 验证正确的 -ss 偏移量（通过直接构造参数）
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(
    reason="Feature-013 retired src/workers/expert_video_task.py (Alembic 0012). "
    "Tests target the old 11-step pipeline that has been replaced by the "
    "split classification/kb_extraction/diagnosis workers."
)

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("COS_SECRET_ID", "test-id")
os.environ.setdefault("COS_SECRET_KEY", "test-key")
os.environ.setdefault("COS_REGION", "ap-guangzhou")
os.environ.setdefault("COS_BUCKET", "test-bucket")

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def _make_subprocess_result(returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    return r


def _capture_ffmpeg_cmd_from_worker(tmp_path, offset_s=0, duration_s=30):
    """Call _split_one_segment directly (mock subprocess) and capture the ffmpeg cmd."""
    from src.workers.expert_video_task import _split_one_segment

    src = tmp_path / "video.mp4"
    src.touch()
    seg_path = tmp_path / "seg_0000_video.mp4"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        seg_path.touch()
        return _make_subprocess_result(0)

    with patch("subprocess.run", side_effect=fake_run):
        _split_one_segment("/usr/bin/ffmpeg", str(src), str(seg_path), offset_s, duration_s)

    return captured["cmd"]


class TestFfmpegCommand:
    """Verify ffmpeg command parameters in _split_one_segment (worker func)."""

    def test_preset_ultrafast_present(self, tmp_path):
        """-preset ultrafast must appear in the ffmpeg command (FR-002)."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "-preset" in cmd, f"'-preset' flag missing: {cmd}"
        preset_idx = cmd.index("-preset")
        assert cmd[preset_idx + 1] == "ultrafast", (
            f"Expected 'ultrafast', got '{cmd[preset_idx + 1]}'"
        )

    def test_no_medium_preset(self, tmp_path):
        """The slow 'medium' preset must NOT appear."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "medium" not in cmd, f"'medium' preset must be removed: {cmd}"

    def test_crf_23_present(self, tmp_path):
        """-crf 23 must still be present (quality control)."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "-crf" in cmd, f"'-crf' flag missing: {cmd}"
        crf_idx = cmd.index("-crf")
        assert cmd[crf_idx + 1] == "23", f"Expected crf=23, got '{cmd[crf_idx + 1]}'"

    def test_no_audio_flag(self, tmp_path):
        """-an must be present (strip audio)."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "-an" in cmd, f"'-an' flag missing: {cmd}"

    def test_scale_filter_present(self, tmp_path):
        """-vf scale=1280:720 must be present."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "-vf" in cmd, f"'-vf' flag missing: {cmd}"
        vf_idx = cmd.index("-vf")
        assert "scale=1280:720" in cmd[vf_idx + 1], (
            f"scale filter missing: {cmd[vf_idx + 1]}"
        )

    def test_libx264_codec(self, tmp_path):
        """-c:v libx264 must be present."""
        cmd = _capture_ffmpeg_cmd_from_worker(tmp_path)
        assert "-c:v" in cmd, f"'-c:v' flag missing: {cmd}"
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "libx264", (
            f"Expected 'libx264', got '{cmd[cv_idx + 1]}'"
        )

    def test_correct_segment_offsets(self, tmp_path):
        """Worker uses correct -ss offset (offset_s passed as argument)."""
        for offset_s in [0, 30, 60]:
            cmd = _capture_ffmpeg_cmd_from_worker(tmp_path, offset_s=offset_s)
            ss_idx = cmd.index("-ss")
            assert cmd[ss_idx + 1] == str(offset_s), (
                f"Expected -ss {offset_s}, got {cmd[ss_idx + 1]}"
            )
