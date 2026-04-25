"""Unit tests for Feature-016 preprocessing.cos_uploader.

ConcurrentUploader must:
- Use ThreadPoolExecutor(max_workers=N) to upload segments in parallel.
- Return Futures the caller can gather.
- Retry each upload up to 3 times on transient failure.
- Map exhausted retries to RuntimeError with ``VIDEO_UPLOAD_FAILED:`` prefix.
- Expose delete_prefix(cos_prefix) for force=true cleanup (F-007a).
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


@pytest.mark.unit
class TestConcurrentUploader:
    def test_uploads_all_segments_via_threadpool(self, tmp_path):
        from src.services.preprocessing import cos_uploader

        # Create 5 local files
        files = []
        for i in range(5):
            f = tmp_path / f"seg_{i:04d}.mp4"
            f.write_bytes(b"x" * 64)
            files.append(f)

        mock_client = MagicMock()
        mock_client.put_object = MagicMock(return_value={"ETag": "abc"})

        with patch.object(
            cos_uploader, "_new_cos_client", return_value=(mock_client, "bucket"),
        ):
            uploader = cos_uploader.ConcurrentUploader(max_workers=2)
            futures = [
                uploader.submit_segment(f, f"preproc/seg_{i:04d}.mp4")
                for i, f in enumerate(files)
            ]
            results = [fut.result(timeout=10) for fut in futures]
            uploader.shutdown()

        assert len(results) == 5
        # put_object must be called once per segment
        assert mock_client.put_object.call_count == 5
        # cos_key sanity check — each file ends up at the requested Key
        keys = {c.kwargs["Key"] for c in mock_client.put_object.call_args_list}
        assert keys == {f"preproc/seg_{i:04d}.mp4" for i in range(5)}

    def test_retries_transient_failure_then_succeeds(self, tmp_path):
        from src.services.preprocessing import cos_uploader

        f = tmp_path / "seg.mp4"
        f.write_bytes(b"data")

        mock_client = MagicMock()
        # First two calls raise, third succeeds.
        mock_client.put_object.side_effect = [
            RuntimeError("network blip"),
            RuntimeError("network blip"),
            {"ETag": "ok"},
        ]

        with patch.object(
            cos_uploader, "_new_cos_client", return_value=(mock_client, "bucket"),
        ), patch.object(
            cos_uploader, "_RETRY_WAIT_SECONDS", 0,  # speed up test
        ):
            uploader = cos_uploader.ConcurrentUploader(max_workers=1)
            fut = uploader.submit_segment(f, "preproc/seg.mp4")
            result = fut.result(timeout=10)
            uploader.shutdown()

        assert result["ETag"] == "ok"
        assert mock_client.put_object.call_count == 3

    def test_exhausted_retries_raises_upload_failed(self, tmp_path):
        from src.services.preprocessing import cos_uploader

        f = tmp_path / "seg.mp4"
        f.write_bytes(b"data")

        mock_client = MagicMock()
        mock_client.put_object.side_effect = RuntimeError("always fail")

        with patch.object(
            cos_uploader, "_new_cos_client", return_value=(mock_client, "bucket"),
        ), patch.object(
            cos_uploader, "_RETRY_WAIT_SECONDS", 0,
        ):
            uploader = cos_uploader.ConcurrentUploader(max_workers=1)
            fut = uploader.submit_segment(f, "preproc/seg.mp4")
            with pytest.raises(RuntimeError, match=r"^VIDEO_UPLOAD_FAILED:"):
                fut.result(timeout=10)
            uploader.shutdown()

        assert mock_client.put_object.call_count == 3

    def test_delete_prefix_lists_then_removes_objects(self):
        from src.services.preprocessing import cos_uploader

        mock_client = MagicMock()
        mock_client.list_objects.return_value = {
            "Contents": [
                {"Key": "preproc/a/jobs/old/seg_0000.mp4"},
                {"Key": "preproc/a/jobs/old/seg_0001.mp4"},
                {"Key": "preproc/a/jobs/old/audio.wav"},
            ],
            "IsTruncated": "false",
        }
        mock_client.delete_objects.return_value = {
            "Deleted": [
                {"Key": "preproc/a/jobs/old/seg_0000.mp4"},
                {"Key": "preproc/a/jobs/old/seg_0001.mp4"},
                {"Key": "preproc/a/jobs/old/audio.wav"},
            ]
        }

        with patch.object(
            cos_uploader, "_new_cos_client", return_value=(mock_client, "bucket"),
        ):
            count = cos_uploader.delete_prefix("preproc/a/jobs/old/")

        assert count == 3
        assert mock_client.list_objects.call_count >= 1
        assert mock_client.delete_objects.call_count == 1
