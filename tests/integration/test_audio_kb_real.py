"""Integration test — Feature 015 audio_kb_extract with synthesized transcript (T012/T013).

Drives ``audio_kb_extract`` with a hand-crafted ``transcript.json`` fixture
and a monkeypatched ``TranscriptTechParser.parse`` so the test can assert
both the happy path (FR-009, FR-010) and the upstream-skipped propagation
path (FR-012) without needing a real LLM endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.coach_video_classification import CoachVideoClassification
from src.models.extraction_job import ExtractionJob, ExtractionJobStatus
from src.models.pipeline_step import PipelineStep, PipelineStepStatus, StepType
from src.models.tech_semantic_segment import TechSemanticSegment


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_kb_task(session_factory):
    import uuid as _uuid

    cos_key = f"tests/f015_us2/video_{_uuid.uuid4().hex[:8]}.mp4"
    task_id = None

    async with session_factory() as session:
        session.add(
            CoachVideoClassification(
                coach_name="F015音频测试",
                course_series="feature015-us2",
                cos_object_key=cos_key,
                filename=cos_key.rsplit("/", 1)[-1],
                tech_category="forehand_topspin",
                tech_tags=[],
                classification_source="rule",
                confidence=1.0,
                name_source="fallback",
                kb_extracted=False,
            )
        )
        task = AnalysisTask(
            task_type=TaskType.kb_extraction,
            video_filename=cos_key.rsplit("/", 1)[-1],
            video_size_bytes=1024,
            video_storage_uri=cos_key,
            status=TaskStatus.pending,
            cos_object_key=cos_key,
            submitted_via="single",
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    yield task_id, cos_key

    async with session_factory() as session:
        await session.execute(delete(AnalysisTask).where(AnalysisTask.id == task_id))
        await session.execute(
            delete(CoachVideoClassification).where(
                CoachVideoClassification.cos_object_key == cos_key
            )
        )
        await session.commit()


def _stub_llm_config(monkeypatch) -> None:
    """Force the audio_kb executor's get_settings() to report a configured
    OpenAI key so the LLM-config gate passes without ever making a real
    network call (TranscriptTechParser.parse is also monkeypatched)."""
    from src.config import Settings
    import src.services.kb_extraction_pipeline.step_executors.audio_kb_extract as mod

    fake_settings = Settings()
    fake_settings.venus_token = None
    fake_settings.venus_base_url = None
    fake_settings.openai_api_key = "test-key-fake"
    monkeypatch.setattr(mod, "get_settings", lambda: fake_settings, raising=True)


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_audio_kb_extract_produces_audio_items_from_synthetic_transcript(
    session_factory, seeded_kb_task, tmp_path, monkeypatch
) -> None:
    """FR-009 / FR-010: audio_kb_extract reads a transcript.json fixture,
    runs TranscriptTechParser (monkeypatched to return a pre-built segment),
    and emits kb_items with source_type='audio' + raw_text_span."""
    task_id, cos_key = seeded_kb_task
    _stub_llm_config(monkeypatch)

    # ── Synthesize transcript.json ────────────────────────────────────────
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        '{"language": "zh", "model_version": "whisper-small", '
        '"sentences": ['
        '  {"start": 0.0, "end": 3.2, "text": "拉球时肘部保持 90 到 120 度", "confidence": 0.88},'
        '  {"start": 3.3, "end": 5.0, "text": "重心前移", "confidence": 0.7}'
        ']}'
    )

    # ── Build TechSemanticSegment fixtures ────────────────────────────────
    segments = [
        TechSemanticSegment(
            start_ms=0,
            end_ms=3200,
            source_sentence="拉球时肘部保持 90 到 120 度",
            dimension="elbow_angle",
            param_min=90.0,
            param_max=120.0,
            param_ideal=105.0,
            unit="°",
            parse_confidence=0.85,
            is_reference_note=False,
        ),
        # Reference note — must be dropped by FR-009 filter.
        TechSemanticSegment(
            start_ms=3300,
            end_ms=5000,
            source_sentence="重心前移",
            dimension=None,
            param_min=None,
            param_max=None,
            param_ideal=None,
            unit=None,
            parse_confidence=0.0,
            is_reference_note=True,
        ),
        # Low-confidence — must be dropped by FR-009 filter (<0.5).
        TechSemanticSegment(
            start_ms=5000,
            end_ms=7000,
            source_sentence="手腕发力",
            dimension="wrist_angle",
            param_min=30.0,
            param_max=60.0,
            param_ideal=45.0,
            unit="°",
            parse_confidence=0.3,
            is_reference_note=False,
        ),
    ]

    from src.services import transcript_tech_parser as parser_mod

    def _fake_parse(self, sentences):
        return segments

    monkeypatch.setattr(
        parser_mod.TranscriptTechParser, "parse", _fake_parse, raising=True
    )

    # ── Seed job + pipeline steps ────────────────────────────────────────
    async with session_factory() as session:
        job = ExtractionJob(
            analysis_task_id=task_id,
            cos_object_key=cos_key,
            tech_category="forehand_topspin",
            status=ExtractionJobStatus.running,
            enable_audio_analysis=True,
            audio_language="zh",
            force=False,
        )
        session.add(job)
        await session.flush()

        session.add(
            PipelineStep(
                job_id=job.id,
                step_type=StepType.download_video,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id,
                step_type=StepType.pose_analysis,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id,
                step_type=StepType.audio_transcription,
                status=PipelineStepStatus.success,
                output_artifact_path=str(transcript_path),
            )
        )
        audio_step_id = None
        for step_type in (
            StepType.visual_kb_extract,
            StepType.audio_kb_extract,
            StepType.merge_kb,
        ):
            ps = PipelineStep(job_id=job.id, step_type=step_type)
            session.add(ps)
            await session.flush()
            if step_type == StepType.audio_kb_extract:
                audio_step_id = ps.id

        await session.commit()
        job_id = job.id

    # ── Execute audio_kb_extract directly ─────────────────────────────────
    from src.services.kb_extraction_pipeline.step_executors import audio_kb_extract

    async with session_factory() as session:
        audio_step = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.id == audio_step_id)
            )
        ).scalar_one()
        fresh_job = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        result = await audio_kb_extract.execute(session, fresh_job, audio_step)

    assert result["status"] == PipelineStepStatus.success
    summary = result["output_summary"]
    assert summary["source_type"] == "audio"
    assert summary["llm_model"], "llm_model must reflect configured backend"
    kb_items = summary["kb_items"]
    # Only the high-confidence, non-reference, dimension-bearing segment survives.
    assert len(kb_items) == 1
    item = kb_items[0]
    assert item["dimension"] == "elbow_angle"
    assert item["source_type"] == "audio"
    assert item["param_min"] == 90.0
    assert item["param_max"] == 120.0
    assert item["param_ideal"] == 105.0
    assert item["unit"] == "°"
    assert item["extraction_confidence"] >= 0.5
    # raw_text_span must be populated from the segment's source sentence.
    assert item.get("raw_text_span") == "拉球时肘部保持 90 到 120 度"
    # Summary counters surface the drop reasons (observability FR-014).
    assert summary["parsed_segments_total"] == 3
    assert summary["dropped_low_confidence"] == 1
    assert summary["dropped_reference_notes"] == 1

    # Cleanup
    async with session_factory() as session:
        await session.execute(delete(ExtractionJob).where(ExtractionJob.id == job_id))
        await session.commit()


async def test_audio_kb_extract_propagates_upstream_skipped(
    session_factory, seeded_kb_task, tmp_path, monkeypatch
) -> None:
    """FR-012: when audio_transcription is skipped, audio_kb_extract inherits
    skipped status without ever touching the LLM. We assert that by
    monkeypatching TranscriptTechParser.parse to raise — if the executor
    invokes it, the test fails."""
    task_id, cos_key = seeded_kb_task
    _stub_llm_config(monkeypatch)

    from src.services import transcript_tech_parser as parser_mod

    def _parse_must_not_run(self, sentences):
        raise AssertionError(
            "audio_kb_extract should have short-circuited on upstream=skipped"
        )

    monkeypatch.setattr(
        parser_mod.TranscriptTechParser, "parse", _parse_must_not_run, raising=True
    )

    async with session_factory() as session:
        job = ExtractionJob(
            analysis_task_id=task_id,
            cos_object_key=cos_key,
            tech_category="forehand_topspin",
            status=ExtractionJobStatus.running,
            enable_audio_analysis=False,
            audio_language="zh",
            force=False,
        )
        session.add(job)
        await session.flush()

        session.add(
            PipelineStep(
                job_id=job.id, step_type=StepType.download_video,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id, step_type=StepType.pose_analysis,
                status=PipelineStepStatus.success,
            )
        )
        session.add(
            PipelineStep(
                job_id=job.id, step_type=StepType.audio_transcription,
                status=PipelineStepStatus.skipped,
                output_summary={"skipped": True, "skip_reason": "disabled_by_request"},
            )
        )
        audio_step_id = None
        for step_type in (
            StepType.visual_kb_extract,
            StepType.audio_kb_extract,
            StepType.merge_kb,
        ):
            ps = PipelineStep(job_id=job.id, step_type=step_type)
            session.add(ps)
            await session.flush()
            if step_type == StepType.audio_kb_extract:
                audio_step_id = ps.id
        await session.commit()
        job_id = job.id

    from src.services.kb_extraction_pipeline.step_executors import audio_kb_extract

    async with session_factory() as session:
        audio_step = (
            await session.execute(
                select(PipelineStep).where(PipelineStep.id == audio_step_id)
            )
        ).scalar_one()
        fresh_job = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
        ).scalar_one()
        result = await audio_kb_extract.execute(session, fresh_job, audio_step)

    assert result["status"] == PipelineStepStatus.skipped
    assert result["output_summary"]["skip_reason"] == "audio_transcription_skipped"
    assert result["output_summary"]["kb_items"] == []

    async with session_factory() as session:
        await session.execute(delete(ExtractionJob).where(ExtractionJob.id == job_id))
        await session.commit()
