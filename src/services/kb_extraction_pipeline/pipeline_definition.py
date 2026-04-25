"""Static DAG definition for Feature 014 KB extraction pipeline.

6 steps, 3 waves:
  wave 1: download_video
  wave 2: pose_analysis ∥ audio_transcription
  wave 3: visual_kb_extract ∥ audio_kb_extract
  wave 4: merge_kb

The dependency graph is hard-coded (not user-defined) — see research.md R1.
"""

from __future__ import annotations

from src.models.pipeline_step import StepType


# ── Dependency graph: step -> list of direct upstream steps ─────────────────

DEPENDENCIES: dict[StepType, list[StepType]] = {
    StepType.download_video: [],
    StepType.pose_analysis: [StepType.download_video],
    StepType.audio_transcription: [StepType.download_video],
    StepType.visual_kb_extract: [StepType.pose_analysis],
    StepType.audio_kb_extract: [StepType.audio_transcription],
    StepType.merge_kb: [StepType.visual_kb_extract, StepType.audio_kb_extract],
}


# ── Topological order (kahn) ────────────────────────────────────────────────

def _compute_topological_order() -> list[StepType]:
    """Return steps in topological order. Raises if the graph has a cycle."""
    remaining: dict[StepType, list[StepType]] = {
        k: list(v) for k, v in DEPENDENCIES.items()
    }
    order: list[StepType] = []
    while remaining:
        ready = [s for s, deps in remaining.items() if not deps]
        if not ready:
            raise ValueError(
                "cycle detected in Feature-014 DAG definition: "
                f"{list(remaining)}"
            )
        # Stable alphabetical to make topology deterministic across runs.
        ready.sort(key=lambda s: s.value)
        for s in ready:
            order.append(s)
            remaining.pop(s)
        for deps in remaining.values():
            for s in ready:
                if s in deps:
                    deps.remove(s)
    return order


TOPOLOGICAL_ORDER: list[StepType] = _compute_topological_order()


# ── I/O vs CPU classification — drives retry policy (FR-021) ────────────────

IO_STEPS: frozenset[StepType] = frozenset({
    StepType.download_video,
    StepType.audio_transcription,
    StepType.audio_kb_extract,
})

CPU_STEPS: frozenset[StepType] = frozenset(DEPENDENCIES.keys()) - IO_STEPS


# ── Helpers ─────────────────────────────────────────────────────────────────

def dependents_of(step: StepType) -> list[StepType]:
    """Return direct *downstream* steps — i.e. steps that list ``step`` as a dep."""
    return [s for s, deps in DEPENDENCIES.items() if step in deps]


def all_step_types() -> list[StepType]:
    """All step types, in topological order — used when creating a job."""
    return list(TOPOLOGICAL_ORDER)
