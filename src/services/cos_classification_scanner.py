"""CosClassificationScanner — scan COS coach video directories and classify each video.

Scans COS_VIDEO_ALL_COCAH path for all .mp4 files, extracts coach_name from
config/coach_directory_map.json, classifies tech_category via TechClassifier,
and upserts records into coach_video_classifications table.

Usage:
  scanner = CosClassificationScanner.from_settings()
  stats = asyncio.run(scanner.scan_full(session))
  print(stats)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class ScanStats:
    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
    error_detail: Optional[str] = None


class CosClassificationScanner:
    """Scan COS coach video directories and classify each video file."""

    def __init__(
        self,
        *,
        coach_map: dict[str, str],
        cos_root_prefix: str,
        tech_classifier,
    ) -> None:
        self._coach_map = coach_map
        self._cos_root_prefix = cos_root_prefix
        self._classifier = tech_classifier

    @classmethod
    def from_settings(cls) -> "CosClassificationScanner":
        """Create from project settings."""
        from src.config import get_settings
        from src.services.tech_classifier import TechClassifier

        settings = get_settings()
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        map_path = os.path.join(base_dir, "config", "coach_directory_map.json")
        with open(map_path, encoding="utf-8") as f:
            coach_map = json.load(f)

        classifier = TechClassifier.from_settings()
        return cls(
            coach_map=coach_map,
            cos_root_prefix=settings.cos_video_all_cocah,
            tech_classifier=classifier,
        )

    def _get_cos_client(self):
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore[import]
        from src.config import get_settings
        settings = get_settings()
        config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id,
            SecretKey=settings.cos_secret_key,
            Scheme="https",
        )
        return CosS3Client(config), settings.cos_bucket

    def _list_all_mp4s(self) -> list[dict]:
        """Paginate through COS and return list of mp4 object metadata dicts."""
        client, bucket = self._get_cos_client()
        prefix = self._cos_root_prefix
        objects = []
        marker = ""

        while True:
            response = client.list_objects(
                Bucket=bucket,
                Prefix=prefix,
                Marker=marker,
                MaxKeys=1000,
            )
            contents = response.get("Contents", [])
            for obj in contents:
                key: str = obj["Key"]
                if key.lower().endswith(".mp4"):
                    objects.append(obj)
            is_truncated = response.get("IsTruncated", "false")
            if is_truncated == "false" or not contents:
                break
            marker = response.get("NextMarker") or contents[-1]["Key"]

        logger.info("COS listing complete: %d mp4 files found", len(objects))
        return objects

    def _extract_course_series(self, cos_object_key: str) -> str:
        """Extract the immediate subdirectory name under cos_root_prefix as course_series."""
        # cos_root_prefix: "charhuang/tt_video/乒乓球合集【较新】/"
        # key: "charhuang/tt_video/乒乓球合集【较新】/课程目录/视频文件.mp4"
        relative = cos_object_key[len(self._cos_root_prefix):]
        parts = relative.split("/")
        if len(parts) >= 2:
            return parts[0]
        return relative

    def _get_coach_name(self, course_series: str) -> tuple[str, str]:
        """Return (coach_name, name_source) for a course_series directory name."""
        coach = self._coach_map.get(course_series)
        if coach:
            return coach, "map"
        return course_series, "fallback"

    async def scan_full(self, session: AsyncSession) -> ScanStats:
        """Full scan: upsert all mp4 files from COS root prefix."""
        from src.models.coach_video_classification import CoachVideoClassification

        stats = ScanStats()
        start = time.monotonic()

        try:
            objects = self._list_all_mp4s()
        except Exception as exc:
            stats.errors += 1
            stats.error_detail = str(exc)
            stats.elapsed_s = time.monotonic() - start
            logger.error("COS listing failed: %s", exc)
            return stats

        total = len(objects)
        logger.info("Starting full scan: %d mp4 files to process", total)

        for i, obj in enumerate(objects):
            cos_key: str = obj["Key"]
            filename = cos_key.rsplit("/", 1)[-1]
            course_series = self._extract_course_series(cos_key)
            coach_name, name_source = self._get_coach_name(course_series)

            try:
                clf = self._classifier.classify(filename, course_series)
            except Exception as exc:
                logger.error("Classification error for %s: %s", cos_key, exc)
                stats.errors += 1
                continue

            try:
                # Check existing record (upsert logic)
                stmt = select(CoachVideoClassification).where(
                    CoachVideoClassification.cos_object_key == cos_key
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # Update existing — skip if manually classified
                    if existing.classification_source == "manual":
                        stats.skipped += 1
                    else:
                        existing.coach_name = coach_name
                        existing.course_series = course_series
                        existing.filename = filename
                        existing.tech_category = clf.tech_category
                        existing.tech_tags = clf.tech_tags
                        existing.raw_tech_desc = clf.raw_tech_desc
                        existing.classification_source = clf.classification_source
                        existing.confidence = clf.confidence
                        existing.name_source = name_source
                        stats.updated += 1
                else:
                    record = CoachVideoClassification(
                        coach_name=coach_name,
                        course_series=course_series,
                        cos_object_key=cos_key,
                        filename=filename,
                        tech_category=clf.tech_category,
                        tech_tags=clf.tech_tags or [],
                        raw_tech_desc=clf.raw_tech_desc,
                        classification_source=clf.classification_source,
                        confidence=clf.confidence,
                        name_source=name_source,
                        kb_extracted=False,
                    )
                    session.add(record)
                    stats.inserted += 1

                stats.scanned += 1

                # Flush every 100 records + log progress
                if stats.scanned % 100 == 0:
                    await session.flush()
                    logger.info(
                        "Scan progress: scanned=%d inserted=%d updated=%d errors=%d / total=%d",
                        stats.scanned, stats.inserted, stats.updated, stats.errors, total,
                    )

            except Exception as exc:
                logger.error("DB upsert error for %s: %s", cos_key, exc)
                stats.errors += 1

        await session.commit()
        stats.elapsed_s = time.monotonic() - start
        logger.info(
            "Full scan complete: scanned=%d inserted=%d updated=%d skipped=%d errors=%d elapsed=%.1fs",
            stats.scanned, stats.inserted, stats.updated, stats.skipped, stats.errors, stats.elapsed_s,
        )
        return stats

    async def scan_incremental(self, session: AsyncSession) -> ScanStats:
        """Incremental scan: only process new files not already in DB."""
        from src.models.coach_video_classification import CoachVideoClassification

        stats = ScanStats()
        start = time.monotonic()

        # Load existing keys from DB
        try:
            result = await session.execute(
                select(CoachVideoClassification.cos_object_key)
            )
            existing_keys: set[str] = {row[0] for row in result.all()}
        except Exception as exc:
            stats.errors += 1
            stats.error_detail = str(exc)
            stats.elapsed_s = time.monotonic() - start
            logger.error("Failed to load existing keys: %s", exc)
            return stats

        logger.info("Incremental scan: %d existing keys in DB", len(existing_keys))

        try:
            objects = self._list_all_mp4s()
        except Exception as exc:
            stats.errors += 1
            stats.error_detail = str(exc)
            stats.elapsed_s = time.monotonic() - start
            logger.error("COS listing failed: %s", exc)
            return stats

        new_objects = [o for o in objects if o["Key"] not in existing_keys]
        logger.info(
            "Incremental scan: %d total, %d new files to process",
            len(objects), len(new_objects),
        )
        stats.skipped = len(objects) - len(new_objects)

        for obj in new_objects:
            cos_key: str = obj["Key"]
            filename = cos_key.rsplit("/", 1)[-1]
            course_series = self._extract_course_series(cos_key)
            coach_name, name_source = self._get_coach_name(course_series)

            try:
                clf = self._classifier.classify(filename, course_series)
            except Exception as exc:
                logger.error("Classification error for %s: %s", cos_key, exc)
                stats.errors += 1
                continue

            try:
                record = CoachVideoClassification(
                    coach_name=coach_name,
                    course_series=course_series,
                    cos_object_key=cos_key,
                    filename=filename,
                    tech_category=clf.tech_category,
                    tech_tags=clf.tech_tags or [],
                    raw_tech_desc=clf.raw_tech_desc,
                    classification_source=clf.classification_source,
                    confidence=clf.confidence,
                    name_source=name_source,
                    kb_extracted=False,
                )
                session.add(record)
                stats.inserted += 1
                stats.scanned += 1

                if stats.scanned % 100 == 0:
                    await session.flush()
                    logger.info(
                        "Incremental scan progress: scanned=%d inserted=%d errors=%d",
                        stats.scanned, stats.inserted, stats.errors,
                    )
            except Exception as exc:
                logger.error("DB insert error for %s: %s", cos_key, exc)
                stats.errors += 1

        await session.commit()
        stats.elapsed_s = time.monotonic() - start
        logger.info(
            "Incremental scan complete: scanned=%d inserted=%d skipped=%d errors=%d elapsed=%.1fs",
            stats.scanned, stats.inserted, stats.skipped, stats.errors, stats.elapsed_s,
        )
        return stats
