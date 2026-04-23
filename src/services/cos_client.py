"""Tencent Cloud COS client wrapper.

Encapsulates all COS SDK interactions so that:
- Business logic (workers) never imports the COS SDK directly
- Unit tests can mock this module without touching real COS
- Credentials are always sourced from config.py (never hardcoded)
"""

from __future__ import annotations

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


def _get_status_code(exc: Exception) -> int | None:
    """Extract HTTP status code from a COS SDK exception."""
    # CosServiceError exposes get_status_code() method
    getter = getattr(exc, "get_status_code", None)
    if callable(getter):
        return getter()
    # Fallback: plain attribute (other exception types)
    return getattr(exc, "status_code", None)


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
        status = _get_status_code(exc)
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
        status = _get_status_code(exc)
        # Clean up partial file if it exists
        if local_path.exists():
            local_path.unlink(missing_ok=True)
        if status == 404:
            raise CosObjectNotFoundError(cos_object_key) from exc
        raise CosDownloadError(cos_object_key, reason=str(exc)) from exc


def list_videos(action_type: str = "all") -> list[dict]:
    """List COS video objects filtered by action type.

    Args:
        action_type: "forehand" | "backhand" | "all"
            - "forehand": files whose name contains any forehand keyword
            - "backhand": files whose name contains any backhand keyword
            - "all":      all video files under cos_video_prefix

    Returns:
        List of dicts with keys: cos_object_key, filename, size_bytes, action_type
        Sorted by filename ascending.
    """
    settings = get_settings()
    client, bucket = _get_cos_client()

    # Build keyword sets from comma-separated config strings
    forehand_kws = [k.strip() for k in settings.forehand_video_keywords.split(",") if k.strip()]
    backhand_kws = [k.strip() for k in settings.backhand_video_keywords.split(",") if k.strip()]

    results = []
    marker = ""
    while True:
        kwargs = dict(Bucket=bucket, Prefix=settings.cos_video_prefix, MaxKeys=200)
        if marker:
            kwargs["Marker"] = marker
        response = client.list_objects(**kwargs)

        for obj in response.get("Contents", []):
            size = int(obj["Size"])
            if size == 0:
                continue  # skip directory placeholders
            key = obj["Key"]
            filename = key.split("/")[-1]
            if not filename.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                continue

            # Determine action type from filename
            is_forehand = any(kw in filename for kw in forehand_kws)
            is_backhand = any(kw in filename for kw in backhand_kws)

            if action_type == "forehand" and not is_forehand:
                continue
            if action_type == "backhand" and not is_backhand:
                continue

            detected = (
                "forehand" if is_forehand and not is_backhand
                else "backhand" if is_backhand and not is_forehand
                else "forehand+backhand" if is_forehand and is_backhand
                else "other"
            )
            results.append({
                "cos_object_key": key,
                "filename": filename,
                "size_bytes": size,
                "action_type": detected,
            })

        if response.get("IsTruncated") == "true":
            marker = response["NextMarker"]
        else:
            break

    results.sort(key=lambda x: x["filename"])
    logger.info(
        "list_videos(action_type=%s): found %d videos under prefix %s",
        action_type, len(results), settings.cos_video_prefix,
    )
    return results


def infer_action_type_hint(cos_object_key: str) -> str | None:
    """Infer a fine-grained action type hint from a COS object key filename.

    Keyword matching is applied in priority order (specific before general).
    Returns None when neither forehand nor backhand keywords are present, or
    when the filename is ambiguous (matches both sides).

    Fine-grained forehand types (checked first):
        forehand_position        — 两点, 跑位, 不定点  (position training — checked before attack)
        forehand_attack          — 攻球
        forehand_chop_long       — 劈长
        forehand_counter         — 快带
        forehand_loop_underspin  — 起下旋
        forehand_flick           — 挑打, 挑球
        forehand_topspin         — 拉球, 连续拉, 发力, 广式
        forehand_general         — fallback for any remaining 正手 match

    Fine-grained backhand types (checked first):
        backhand_topspin         — 反手拉球, 反手拉
        backhand_flick           — 弹, 拨, 反拉
        backhand_push            — 推, 挡
        backhand_general         — fallback for any remaining 反手 match
    """
    filename = cos_object_key.split("/")[-1]

    is_forehand = "正手" in filename or "forehand" in filename.lower()
    is_backhand = "反手" in filename or "backhand" in filename.lower()
    # "正反手" (正反 = both forehand and backhand combined) — treat as ambiguous
    is_combined = "正反手" in filename or "正反" in filename

    # Ambiguous — both sides present (e.g. 正反手综合)
    if (is_forehand and is_backhand) or is_combined:
        return None

    # ── Forehand fine-grained (specific → general) ───────────────────────────
    if is_forehand:
        # Position/footwork drills before attack (titles often combine both)
        if "两点" in filename or "跑位" in filename or "不定点" in filename:
            return "forehand_position"
        if "攻球" in filename:
            return "forehand_attack"
        if "劈长" in filename:
            return "forehand_chop_long"
        if "快带" in filename:
            return "forehand_counter"
        if "起下旋" in filename:
            return "forehand_loop_underspin"
        if "挑打" in filename or "挑球" in filename:
            return "forehand_flick"
        if ("拉球" in filename or "连续拉" in filename
                or "发力拉" in filename or "广式" in filename):
            return "forehand_topspin"
        return "forehand_general"

    # ── Backhand fine-grained (specific → general) ───────────────────────────
    if is_backhand:
        if "拉球" in filename or "拉" in filename:
            return "backhand_topspin"
        if "弹" in filename or "拨" in filename or "反拉" in filename:
            return "backhand_flick"
        if "推" in filename or "挡" in filename:
            return "backhand_push"
        return "backhand_general"

    return None  # untagged — no filtering


def cleanup_temp_file(path: Path) -> None:
    """Delete a temporary file created by :func:`download_to_temp`.

    Silently ignores missing files (idempotent).
    """
    try:
        path.unlink(missing_ok=True)
        logger.debug("Cleaned up temp file: %s", path)
    except OSError as exc:
        logger.warning("Failed to clean up temp file %s: %s", path, exc)
