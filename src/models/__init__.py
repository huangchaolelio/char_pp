"""ORM models — import all to ensure Alembic can discover metadata."""

from src.models.analysis_task import AnalysisTask, TaskStatus, TaskType
from src.models.athlete_motion_analysis import AthleteActionType, AthleteMotionAnalysis
from src.models.coaching_advice import CoachingAdvice, ReliabilityLevel
from src.models.deviation_report import DeviationDirection, DeviationReport
from src.models.expert_tech_point import ActionType, ExpertTechPoint
from src.models.tech_knowledge_base import KBStatus, TechKnowledgeBase

__all__ = [
    "AnalysisTask",
    "TaskStatus",
    "TaskType",
    "AthleteMotionAnalysis",
    "AthleteActionType",
    "CoachingAdvice",
    "ReliabilityLevel",
    "DeviationReport",
    "DeviationDirection",
    "ExpertTechPoint",
    "ActionType",
    "TechKnowledgeBase",
    "KBStatus",
]
