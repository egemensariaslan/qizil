"""Optimization passes."""

from .algebra import SynthesisPolicy
from .cancellation import CancellationPass
from .cliffordt import CliffordTPass
from .commutation import CommutationPass
from .manager import (
    MAX_ITERATIONS,
    PASS_REGISTRY,
    PIPELINES,
    PassManager,
    PipelineReport,
    build_pipeline,
)
from .rewrite import Pass, PassContext, PassStats
from .rotation import RotationMergePass

__all__ = [
    "CancellationPass",
    "CliffordTPass",
    "CommutationPass",
    "RotationMergePass",
    "Pass",
    "PassContext",
    "PassStats",
    "PassManager",
    "PipelineReport",
    "PASS_REGISTRY",
    "PIPELINES",
    "MAX_ITERATIONS",
    "SynthesisPolicy",
    "build_pipeline",
]
