"""Diagnosis scorer — pure functions for deviation level and score computation.

Scoring rules (AD-003):
  half_width = (max - min) / 2
  center     = (min + max) / 2
  distance   = |measured - center|

  distance <= half_width              → ok,          score = 100
  half_width < d <= 1.5 * half_width  → slight,      score linearly [100, 60]
  d > 1.5 * half_width                → significant, score linearly [60, 0]
    (score decreases as distance increases beyond 1.5x)

  overall_score = mean of all dimension scores (0 if empty list)

Tunable constants are defined at module level.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Tunable scoring constants
# ---------------------------------------------------------------------------

SCORE_OK: float = 100.0           # score when within [min, max]
SCORE_SLIGHT_BOUNDARY: float = 60.0  # score at the boundary between slight and significant
SCORE_SIGNIFICANT_MIN: float = 0.0   # minimum possible score
SLIGHT_OUTER_MULTIPLIER: float = 1.5  # half-width multiplier for slight/significant boundary


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DeviationLevel(str, enum.Enum):
    ok = "ok"
    slight = "slight"
    significant = "significant"


class DeviationDirection(str, enum.Enum):
    above = "above"
    below = "below"
    none = "none"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DimensionScore:
    dimension: str
    measured_value: float
    ideal_value: float
    standard_min: float
    standard_max: float
    unit: str
    score: float
    deviation_level: DeviationLevel
    deviation_direction: DeviationDirection


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_dimension_score(
    measured: float,
    std_min: float,
    std_max: float,
    ideal: float,
    unit: str,
    dimension: str,
) -> DimensionScore:
    """Compute score and deviation info for a single dimension.

    Handles edge case of std_min == std_max (zero half-width) without
    raising ZeroDivisionError.
    """
    half_width = (std_max - std_min) / 2.0
    center = (std_min + std_max) / 2.0
    distance = abs(measured - center)

    # Determine direction
    if measured > std_max:
        direction = DeviationDirection.above
    elif measured < std_min:
        direction = DeviationDirection.below
    else:
        direction = DeviationDirection.none

    # Handle degenerate case: min == max
    if half_width == 0.0:
        if distance == 0.0:
            level = DeviationLevel.ok
            score = SCORE_OK
        else:
            level = DeviationLevel.significant
            score = SCORE_SIGNIFICANT_MIN
        return DimensionScore(
            dimension=dimension,
            measured_value=measured,
            ideal_value=ideal,
            standard_min=std_min,
            standard_max=std_max,
            unit=unit,
            score=score,
            deviation_level=level,
            deviation_direction=direction,
        )

    slight_outer = SLIGHT_OUTER_MULTIPLIER * half_width  # 1.5 * hw

    if distance <= half_width:
        # Within range
        level = DeviationLevel.ok
        score = SCORE_OK
    elif distance <= slight_outer:
        # Slight deviation: linear interpolation from 100 at hw to 60 at 1.5*hw
        t = (distance - half_width) / (slight_outer - half_width)  # 0 → 1
        score = SCORE_OK + t * (SCORE_SLIGHT_BOUNDARY - SCORE_OK)  # 100 → 60
        level = DeviationLevel.slight
    else:
        # Significant deviation: linear interpolation from 60 at 1.5*hw toward 0
        # Use a decay factor; score approaches 0 at distance = 4 * half_width
        decay_width = 4.0 * half_width  # full decay at 4x half-width
        excess = distance - slight_outer
        max_excess = decay_width - slight_outer  # = 2.5 * hw
        if max_excess <= 0:
            score = SCORE_SIGNIFICANT_MIN
        else:
            t = min(1.0, excess / max_excess)
            score = SCORE_SLIGHT_BOUNDARY * (1.0 - t)
        score = max(SCORE_SIGNIFICANT_MIN, score)
        level = DeviationLevel.significant

    return DimensionScore(
        dimension=dimension,
        measured_value=measured,
        ideal_value=ideal,
        standard_min=std_min,
        standard_max=std_max,
        unit=unit,
        score=score,
        deviation_level=level,
        deviation_direction=direction,
    )


def compute_overall_score(dimension_scores: list[DimensionScore]) -> float:
    """Compute equal-weight average of all dimension scores.

    Returns 0.0 if the list is empty.
    """
    if not dimension_scores:
        return 0.0
    return sum(ds.score for ds in dimension_scores) / len(dimension_scores)
