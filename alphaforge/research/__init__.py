"""Lớp nghiên cứu: dự án, giả thuyết, thí nghiệm, trí nhớ và phả hệ alpha."""

from .advisor import (
    ResearchAdvisor,
    RuleBasedResearchAdvisor,
    Suggestion,
)
from .experiment import ExperimentDesign, ExperimentEngine, ExperimentError
from .gap import Gap, ResearchGap
from .memory import GenerationContext, ResearchMemory, ResearchProfile
from .models import (
    AlphaLineage,
    Experiment,
    ExperimentVariant,
    Hypothesis,
    ResearchProject,
)
from .plan import GenerationPlan, PlanError
from .priority import PriorityScore, PriorityWeights, ResearchPriority
from .report import ExperimentReport, evidence_level
from .store import ResearchStore

__all__ = [
    "ResearchProject",
    "Hypothesis",
    "Experiment",
    "ExperimentVariant",
    "AlphaLineage",
    "ResearchStore",
    "ResearchMemory",
    "ResearchProfile",
    "GenerationContext",
    "ResearchGap",
    "Gap",
    "ResearchPriority",
    "PriorityScore",
    "PriorityWeights",
    "GenerationPlan",
    "PlanError",
    "ExperimentDesign",
    "ExperimentEngine",
    "ExperimentError",
    "ResearchAdvisor",
    "RuleBasedResearchAdvisor",
    "Suggestion",
    "ExperimentReport",
    "evidence_level",
]
