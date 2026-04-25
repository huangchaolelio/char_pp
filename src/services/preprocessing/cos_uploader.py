"""Feature-016 — ThreadPool concurrent uploader for preprocessing segments.

Why a separate uploader (instead of reusing ``src.services.cos_client``):
- We need a **stable** concurrent primitive keyed to the project-level
  ``preprocessing_upload_concurrency`` setting.
- Each worker thread needs its *own* ``CosS3Client`` — the SDK is not
  documented as thread-safe for shared instances.
- Retry policy here is fixed-wait (3 × 30s) as required by R3; this is a
  narrower policy than the generic tenacity stack in kb_extraction_pipeline.

Also exposes ``delete_prefix(prefix)`` for FR-007a ``force=true`` cleanup.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from src.config import get_settings
from src.services.preprocessing import error_codes


logger = logging.getLogger(__name__)


# Overridable from unit tests to avoid real 30s sleeps.
_RETRY_WAIT_SECONDS = 30
_RETRY_ATTEMPTS = 3


def _new_cos_client() -> tuple[Any, str]:
    """Build a fresh ``CosS3Client`` per caller thread."""
    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "cos-python-sdk-v5 is not installed"
        ) from exc

    settings = get_settings()
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.cos_secret_id,
        SecretKey=settings.cos_secret_key,
        Scheme="https",
    )
    return CosS3Client(config), settings.cos_bucket


class _PerThreadCosClient(threading.local):
    """Lazy per-thread COS client."""

    def __init__(self) -> None:
        super().__init__()
        self.client: Optional[Any] = None
        self.bucket: Optional[str] = None

    def get(self) -> tuple[Any, str]:
        if self.client is None:
            self.client, self.bucket = _new_cos_client()
        return self.client, self.bucket


class ConcurrentUploader:
    """Upload segments (and audio) to COS via a bounded ThreadPoolExecutor.

    The caller submits files via :meth:`submit_segment`; each call returns a
    ``Future`` whose result is the raw COS SDK response dict (includes ETag).
    """

    def __init__(self, *, max_workers: int | None = None) -> None:
        workers = max_workers or get_settings().preprocessing_upload_concurrency
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._per_thread = _PerThreadCosClient()

    # ── Public ──────────────────────────────────────────────────────────────

    def submit_segment(self, local_path: Path, cos_key: str) -> Future:
        """Enqueue one file for upload; return its ``Future``."""
        return self._pool.submit(self._upload_one, local_path, cos_key)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)

    # ── Internals ───────────────────────────────────────────────────────────

    def _upload_one(self, local_path: Path, cos_key: str) -> dict[str, Any]:
        client, bucket = self._per_thread.get()
        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                with open(local_path, "rb") as fh:
                    resp = client.put_object(
                        Bucket=bucket, Key=cos_key, Body=fh,
                    )
                logger.info(
                    "uploaded → cos://%s/%s (%d bytes, attempt=%d)",
                    bucket, cos_key, local_path.stat().st_size, attempt,
                )
                return resp
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "upload attempt %d/%d failed for %s → %s: %s",
                    attempt, _RETRY_ATTEMPTS, local_path, cos_key, exc,
                )
                if attempt < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_WAIT_SECONDS)
        # Exhausted retries
        raise RuntimeError(
            error_codes.format_error(
                error_codes.VIDEO_UPLOAD_FAILED,
                f"{cos_key}: {last_exc}",
            )
        )


def delete_prefix(prefix: str) -> int:
    """Delete every object under ``prefix`` in the configured bucket.

    Used by ``preprocessing_service.create_or_reuse(force=True)`` to clear the
    previous success job's COS artefacts before inserting the replacement.

    Returns:
        Number of objects deleted.
    """
    client, bucket = _new_cos_client()
    deleted = 0
    marker: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if marker:
            kwargs["Marker"] = marker
        resp = client.list_objects(**kwargs)
        contents = resp.get("Contents") or []
        if not contents:
            break
        to_delete = {
            "Quiet": "false",
            "Object": [{"Key": obj["Key"]} for obj in contents],
        }
        del_resp = client.delete_objects(Bucket=bucket, Delete=to_delete)
        deleted += len(del_resp.get("Deleted", []))
        if resp.get("IsTruncated") != "true":
            break
        marker = resp.get("NextMarker") or (contents[-1]["Key"])
    logger.info("delete_prefix %s → deleted=%d", prefix, deleted)
    return deleted
