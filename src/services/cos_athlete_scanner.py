"""CosAthleteScanner — Feature-020 运动员视频扫描器.

与 :class:`src.services.cos_classification_scanner.CosClassificationScanner` 结构
镜像，但在实体层完全独立：

- 读取根路径：``settings.cos_video_all_athlete``（与教练侧物理隔离）
- 目录 → 姓名映射：``config/athlete_directory_map.json``（键以下划线开头的伪字段跳过）
- 扫描结果写入 ``athletes`` + ``athlete_video_classifications`` 两表
- **不触碰** ``coaches`` / ``coach_video_classifications`` 表（SC-006）

复用底层能力：``TechClassifier`` 21 类字典；其余完全独立。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import AppException, ErrorCode

logger = logging.getLogger(__name__)


@dataclass
class AthleteScanStats:
    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
    error_detail: Optional[str] = None


class CosAthleteScanner:
    """扫描运动员 COS 根路径，分类每条视频并写运动员侧两张表."""

    def __init__(
        self,
        *,
        athlete_map: dict[str, str],
        cos_root_prefix: str,
        tech_classifier,
    ) -> None:
        self._cos_root_prefix = cos_root_prefix
        self._classifier = tech_classifier

        # 与教练侧一样的同名后缀去重：第 1 个保持原名，后续加 `_2 / _3`
        self._dir_to_unique_athlete: dict[str, str] = {}
        self._athlete_bio_map: dict[str, str] = {}
        name_counter: dict[str, int] = {}
        # 跳过以下划线开头的伪字段（_README 等；见 config/athlete_directory_map.json）
        for directory, base_name in athlete_map.items():
            if directory.startswith("_"):
                continue
            count = name_counter.get(base_name, 0) + 1
            name_counter[base_name] = count
            unique_name = base_name if count == 1 else f"{base_name}_{count}"
            self._dir_to_unique_athlete[directory] = unique_name
            self._athlete_bio_map[unique_name] = directory

    # ── Factory ─────────────────────────────────────────────────────────
    @classmethod
    def from_settings(cls) -> "CosAthleteScanner":
        """从项目配置构造扫描器. 未找到映射配置文件 → 抛 `ATHLETE_DIRECTORY_MAP_MISSING`."""
        from src.config import get_settings
        from src.services.tech_classifier import TechClassifier

        settings = get_settings()
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        map_path = os.path.join(base_dir, "config", "athlete_directory_map.json")
        if not os.path.isfile(map_path):
            raise AppException(
                ErrorCode.ATHLETE_DIRECTORY_MAP_MISSING,
                details={"expected_path": map_path},
            )
        with open(map_path, encoding="utf-8") as f:
            athlete_map = json.load(f)

        classifier = TechClassifier.from_settings()
        return cls(
            athlete_map=athlete_map,
            cos_root_prefix=settings.cos_video_all_athlete,
            tech_classifier=classifier,
        )

    # ── COS client ──────────────────────────────────────────────────────
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
        """分页遍历 COS，返回所有非零字节 .mp4 对象元数据.

        COS 凭证错误 / 根路径不可读 → 抛 ``ATHLETE_ROOT_UNREADABLE``。
        """
        try:
            client, bucket = self._get_cos_client()
        except Exception as exc:
            raise AppException(
                ErrorCode.ATHLETE_ROOT_UNREADABLE,
                details={
                    "root_prefix": self._cos_root_prefix,
                    "upstream_error_code": exc.__class__.__name__,
                    "upstream_message": str(exc),
                },
            ) from exc

        prefix = self._cos_root_prefix
        objects: list[dict] = []
        marker = ""

        try:
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
                    if int(obj.get("Size", 0)) == 0:
                        continue
                    if key.lower().endswith(".mp4"):
                        objects.append(obj)
                is_truncated = response.get("IsTruncated", "false")
                if is_truncated == "false" or not contents:
                    break
                marker = response.get("NextMarker") or contents[-1]["Key"]
        except Exception as exc:
            raise AppException(
                ErrorCode.ATHLETE_ROOT_UNREADABLE,
                details={
                    "root_prefix": prefix,
                    "upstream_error_code": exc.__class__.__name__,
                    "upstream_message": str(exc),
                },
            ) from exc

        logger.info("COS athlete listing: %d mp4 files under %s", len(objects), prefix)
        return objects

    # ── Directory → name ────────────────────────────────────────────────
    def _extract_directory(self, cos_object_key: str) -> str:
        """提取 cos_root_prefix 下的第 1 段目录名作为运动员目录."""
        if not cos_object_key.startswith(self._cos_root_prefix):
            # 不太可能，但做一层保险
            return cos_object_key.rsplit("/", 1)[0].split("/")[-1]
        relative = cos_object_key[len(self._cos_root_prefix):]
        parts = relative.split("/")
        if len(parts) >= 2:
            return parts[0]
        return relative

    def _get_athlete_name(self, directory: str) -> tuple[str, str]:
        """Return (unique_athlete_name, name_source)."""
        unique = self._dir_to_unique_athlete.get(directory)
        if unique:
            return unique, "map"
        # Fallback: 用目录名本身，同样走同名后缀规则
        count = sum(
            1 for base in self._dir_to_unique_athlete.values() if base.startswith(directory)
        )
        unique = directory if count == 0 else f"{directory}_{count + 1}"
        self._dir_to_unique_athlete.setdefault(directory, unique)
        self._athlete_bio_map.setdefault(unique, directory)
        return unique, "fallback"

    def _get_athlete_bio(self, athlete_name: str) -> Optional[str]:
        return self._athlete_bio_map.get(athlete_name)

    # ── Athlete upsert ──────────────────────────────────────────────────
    async def _upsert_athlete(self, session: AsyncSession, athlete_name: str) -> "Athlete":  # type: ignore[name-defined]  # noqa: F821
        """Insert or update athlete row; returns the Athlete ORM instance."""
        from src.models.athlete import Athlete

        result = await session.execute(
            select(Athlete).where(Athlete.name == athlete_name)
        )
        existing = result.scalar_one_or_none()
        bio = self._get_athlete_bio(athlete_name)
        if existing is None:
            new_row = Athlete(name=athlete_name, bio=bio, created_via="athlete_scan")
            session.add(new_row)
            await session.flush()
            logger.info("Auto-inserted new athlete: %s (bio=%s)", athlete_name, bio)
            return new_row
        if existing.bio is None and bio is not None:
            existing.bio = bio
            logger.info("Backfilled bio for athlete: %s", athlete_name)
        return existing

    # ── Full scan ───────────────────────────────────────────────────────
    async def scan_full(self, session: AsyncSession) -> AthleteScanStats:
        from src.models.athlete_video_classification import AthleteVideoClassification

        stats = AthleteScanStats()
        start = time.monotonic()

        try:
            objects = self._list_all_mp4s()
        except AppException as exc:
            stats.errors += 1
            stats.error_detail = f"{exc.code.value}: {exc.message}"
            stats.elapsed_s = time.monotonic() - start
            return stats

        seen_athletes: dict[str, "Athlete"] = {}  # type: ignore[name-defined]  # noqa: F821

        for obj in objects:
            cos_key: str = obj["Key"]
            filename = cos_key.rsplit("/", 1)[-1]
            directory = self._extract_directory(cos_key)
            athlete_name, name_source = self._get_athlete_name(directory)

            try:
                clf = self._classifier.classify(filename, directory)
            except Exception as exc:
                logger.error("Classification error for %s: %s", cos_key, exc)
                stats.errors += 1
                continue

            try:
                # 同名运动员上游缓存
                if athlete_name not in seen_athletes:
                    seen_athletes[athlete_name] = await self._upsert_athlete(
                        session, athlete_name
                    )
                athlete_row = seen_athletes[athlete_name]

                # Upsert by cos_object_key (UNIQUE)
                stmt = select(AthleteVideoClassification).where(
                    AthleteVideoClassification.cos_object_key == cos_key
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()

                if existing:
                    existing.athlete_id = athlete_row.id
                    existing.athlete_name = athlete_name
                    existing.name_source = name_source
                    existing.tech_category = clf.tech_category
                    existing.classification_source = clf.classification_source
                    existing.classification_confidence = float(clf.confidence)
                    stats.updated += 1
                else:
                    record = AthleteVideoClassification(
                        cos_object_key=cos_key,
                        athlete_id=athlete_row.id,
                        athlete_name=athlete_name,
                        name_source=name_source,
                        tech_category=clf.tech_category,
                        classification_source=clf.classification_source,
                        classification_confidence=float(clf.confidence),
                        preprocessed=False,
                    )
                    session.add(record)
                    stats.inserted += 1
                stats.scanned += 1

                if stats.scanned % 100 == 0:
                    await session.flush()
                    logger.info(
                        "Athlete scan progress: scanned=%d inserted=%d updated=%d errors=%d",
                        stats.scanned, stats.inserted, stats.updated, stats.errors,
                    )
            except Exception as exc:
                logger.error("DB upsert error for %s: %s", cos_key, exc)
                stats.errors += 1

        await session.commit()
        stats.elapsed_s = time.monotonic() - start
        logger.info(
            "Athlete full scan complete: scanned=%d inserted=%d updated=%d skipped=%d "
            "errors=%d elapsed=%.1fs",
            stats.scanned, stats.inserted, stats.updated, stats.skipped,
            stats.errors, stats.elapsed_s,
        )
        return stats

    # ── Incremental scan ────────────────────────────────────────────────
    async def scan_incremental(self, session: AsyncSession) -> AthleteScanStats:
        from src.models.athlete_video_classification import AthleteVideoClassification

        stats = AthleteScanStats()
        start = time.monotonic()

        try:
            result = await session.execute(
                select(AthleteVideoClassification.cos_object_key)
            )
            existing_keys: set[str] = {row[0] for row in result.all()}
        except Exception as exc:
            stats.errors += 1
            stats.error_detail = str(exc)
            stats.elapsed_s = time.monotonic() - start
            return stats

        try:
            objects = self._list_all_mp4s()
        except AppException as exc:
            stats.errors += 1
            stats.error_detail = f"{exc.code.value}: {exc.message}"
            stats.elapsed_s = time.monotonic() - start
            return stats

        new_objects = [o for o in objects if o["Key"] not in existing_keys]
        stats.skipped = len(objects) - len(new_objects)

        seen_athletes: dict[str, "Athlete"] = {}  # type: ignore[name-defined]  # noqa: F821

        for obj in new_objects:
            cos_key: str = obj["Key"]
            filename = cos_key.rsplit("/", 1)[-1]
            directory = self._extract_directory(cos_key)
            athlete_name, name_source = self._get_athlete_name(directory)

            try:
                clf = self._classifier.classify(filename, directory)
            except Exception as exc:
                logger.error("Classification error for %s: %s", cos_key, exc)
                stats.errors += 1
                continue

            try:
                if athlete_name not in seen_athletes:
                    seen_athletes[athlete_name] = await self._upsert_athlete(
                        session, athlete_name
                    )
                athlete_row = seen_athletes[athlete_name]

                record = AthleteVideoClassification(
                    cos_object_key=cos_key,
                    athlete_id=athlete_row.id,
                    athlete_name=athlete_name,
                    name_source=name_source,
                    tech_category=clf.tech_category,
                    classification_source=clf.classification_source,
                    classification_confidence=float(clf.confidence),
                    preprocessed=False,
                )
                session.add(record)
                stats.inserted += 1
                stats.scanned += 1
            except Exception as exc:
                logger.error("DB insert error for %s: %s", cos_key, exc)
                stats.errors += 1

        await session.commit()
        stats.elapsed_s = time.monotonic() - start
        logger.info(
            "Athlete incremental scan complete: scanned=%d inserted=%d skipped=%d "
            "errors=%d elapsed=%.1fs",
            stats.scanned, stats.inserted, stats.skipped, stats.errors, stats.elapsed_s,
        )
        return stats
