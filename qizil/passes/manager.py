"""Pass registry, optimization pipelines and the fixed-point driver."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ir.module import Module
from .cancellation import CancellationPass
from .cliffordt import CliffordTPass
from .commutation import CommutationPass
from .rewrite import Pass, PassContext, PassStats
from .rotation import RotationMergePass

__all__ = [
    "PASS_REGISTRY",
    "PIPELINES",
    "PassManager",
    "PipelineReport",
    "build_pipeline",
]

PASS_REGISTRY: dict[str, type[Pass]] = {
    p.name: p
    for p in (CancellationPass, RotationMergePass, CommutationPass, CliffordTPass)
}

#: ``-O<level>`` pipelines.
PIPELINES: dict[int, list[str]] = {
    0: [],
    1: ["cancel", "merge-rotations"],
    2: ["cancel", "merge-rotations", "commute", "clifford-t"],
    3: ["cancel", "merge-rotations", "commute", "clifford-t"],
}

#: How many times the pipeline may be repeated while it keeps making progress.
MAX_ITERATIONS: dict[int, int] = {0: 0, 1: 2, 2: 8, 3: 24}


@dataclass
class PipelineReport:
    iterations: int = 0
    per_pass: dict[str, PassStats] = field(default_factory=dict)

    @property
    def total_rewrites(self) -> int:
        return sum(s.rewrites for s in self.per_pass.values())

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "passes": [s.to_dict() for s in self.per_pass.values()],
            "total_rewrites": self.total_rewrites,
        }


def build_pipeline(
    level: int = 2,
    only: list[str] | None = None,
    disable: list[str] | None = None,
) -> list[Pass]:
    """Instantiate the passes for an optimization level."""
    if only:
        names = list(only)
    else:
        if level not in PIPELINES:
            raise ValueError(f"unknown optimization level: -O{level}")
        names = list(PIPELINES[level])
    if disable:
        names = [n for n in names if n not in set(disable)]
    unknown = [n for n in names if n not in PASS_REGISTRY]
    if unknown:
        raise ValueError(
            f"unknown pass(es): {', '.join(unknown)}; "
            f"available: {', '.join(sorted(PASS_REGISTRY))}"
        )
    return [PASS_REGISTRY[n]() for n in names]


class PassManager:
    """Runs a pipeline until it stops changing the module."""

    def __init__(self, passes: list[Pass], max_iterations: int = 8):
        self.passes = passes
        self.max_iterations = max_iterations

    def run(self, module: Module, ctx: PassContext) -> PipelineReport:
        report = PipelineReport()
        for stage in self.passes:
            report.per_pass.setdefault(stage.name, PassStats(stage.name))
        for _ in range(self.max_iterations):
            report.iterations += 1
            changed = False
            for stage in self.passes:
                stats = stage.run(module, ctx)
                report.per_pass[stage.name].merge(stats)
                changed = changed or stats.changed
            if not changed:
                break
        return report
