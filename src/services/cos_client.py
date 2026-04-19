"""Tencent Cloud COS client wrapper.

Encapsulates all COS SDK interactions so that:
- Business logic (workers) never imports the COS SDK directly
- Unit tests can mock this module without touching real COS
- Credentials are always sourced from config.py (never hardcoded)
"""

import logging
import uuid
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger(__name__)


class CosObjectNotFoundError(Exception):
    """Raised when the specified COS object does not exist or is inaccessible."""

    def __init__(self, cos_object_key: str) -> None:
        super().__init__(f"COS object not found or inaccessible: {cos_object_key}")
        self.cos_object_key = cos_object_key


class CosDownloadError(Exception):
    """Raised when a COS download fails due to network or permission issues."""

    def __init__(self, cos_object_key: str, reason: str = "") -> None:
        super().__init__(f"COS download failed for {cos_object_key}: {reason}")
        self.cos_object_key = cos_object_key
        self.reason = reason


def _get_cos_client():
    """Build and return a qcloud_cos.CosS3Client instance."""
    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "cos-python-sdk-v5 is not installed. Run: pip install cos-python-sdk-v5"
        ) from exc

    settings = get_settings()
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.cos_secret_id,
        SecretKey=settings.cos_secret_key,
        Scheme="https",
    )
    return CosS3Client(config), settings.cos_bucket


def object_exists(cos_object_key: str) -> bool:
    """Check whether *cos_object_key* exists in the configured bucket.

    Returns True if the object is accessible, False otherwise.
    Does NOT raise — callers decide how to handle a missing object.
    """
    try:
        client, bucket = _get_cos_client()
        client.head_object(Bucket=bucket, Key=cos_object_key)
        logger.debug("COS object exists: %s", cos_object_key)
        return True
    except Exception as exc:
        # COS SDK raises CosServiceError with status_code 404 for missing objects
        status = getattr(exc, "status_code", None) or getattr(exc, "get_status_code", lambda: None)()
        if status == 404:
            logger.debug("COS object not found: %s", cos_object_key)
            return False
        # Any other error (auth, network) — treat as non-existent and log warning
        logger.warning("COS head_object error for %s: %s", cos_object_key, exc)
        return False


def download_to_temp(cos_object_key: str) -> Path:
    """Download *cos_object_key* from COS to a local temporary file.

    The file is placed in config.TMP_DIR with a unique name so concurrent
    tasks never collide.

    Returns:
        Path to the downloaded local file.

    Raises:
        CosObjectNotFoundError: if the object does not exist.
        CosDownloadError: if the download fails for any other reason.
    """
    settings = get_settings()
    tmp_dir = settings.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Derive a safe local filename: preserve the extension from the COS key
    suffix = Path(cos_object_key).suffix or ".mp4"
    local_path = tmp_dir / f"{uuid.uuid4().hex}{suffix}"

    logger.info("Downloading COS object %s → %s", cos_object_key, local_path)
    try:
        client, bucket = _get_cos_client()
        response = client.get_object(Bucket=bucket, Key=cos_object_key)
        response["Body"].get_stream_to_file(str(local_path))
        logger.info(
            "COS download complete: %s (%.1f KB)",
            local_path,
            local_path.stat().st_size / 1024,
        )
        return local_path
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(exc, "get_status_code", lambda: None)()
        # Clean up partial file if it exists
        if local_path.exists():
            local_path.unlink(missing_ok=True)
        if status == 404:
            raise CosObjectNotFoundError(cos_object_key) from exc
        raise CosDownloadError(cos_object_key, reason=str(exc)) from exc


def cleanup_temp_file(path: Path) -> None:
    """Delete a temporary file created by :func:`download_to_temp`.

    Silently ignores missing files (idempotent).
    """
    try:
        path.unlink(missing_ok=True)
        logger.debug("Cleaned up temp file: %s", path)
    except OSError as exc:
        logger.warning("Failed to clean up temp file %s: %s", path, exc)
