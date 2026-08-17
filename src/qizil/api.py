"""Public Python API.

    >>> from qizil import optimize
    >>> result = optimize("circuit.ll", level=2)
    >>> print(result.after.gates, "gates left")
    >>> open("out.ll", "w").write(result.to_ll())
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Union

from .analysis.estimator import EstimateComparison, compare, estimate
from .analysis.metrics import Metrics, measure
from .ir.bitcode import bitcode_to_ll, is_bitcode, llvm_verify, to_bitcode
from .ir.module import Module
from .ir.parser import parse_ll
from .passes import (
    MAX_ITERATIONS,
    PassContext,
    PassManager,
    PipelineReport,
    SynthesisPolicy,
    build_pipeline,
)

__all__ = [
    "Source",
    "read_source",
    "parse",
    "optimize",
    "OptimizationResult",
    "estimate",
    "measure",
]

Source = Union[str, bytes, os.PathLike, Module]


def _looks_like_path(text: str) -> bool:
    """A single line with no IR syntax in it is meant as a filename."""
    return "\n" not in text and not text.lstrip().startswith((";", "%", "@", "!"))


def read_source(source: Source) -> str:
    """Get textual IR out of a path, a bitcode/IR blob, or IR text."""
    if isinstance(source, Module):
        return source.to_ll()
    if isinstance(source, bytes):
        return bitcode_to_ll(source) if is_bitcode(source) else source.decode("utf-8")
    path = os.fspath(source)
    if _looks_like_path(path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"no such file: {path}")
        with open(path, "rb") as fh:
            data = fh.read()
        return (
            bitcode_to_ll(data, name=path) if is_bitcode(data) else data.decode("utf-8")
        )
    return path  # already IR text


def _source_name(source: Source) -> str | None:
    """The file this module came from, when it came from a file."""
    if isinstance(source, (bytes, Module)):
        return None
    candidate = os.fspath(source)
    return candidate if _looks_like_path(candidate) else None


def parse(source: Source) -> Module:
    """Parse any accepted source into a :class:`~qizil.ir.module.Module`."""
    if isinstance(source, Module):
        return source
    module = parse_ll(read_source(source))
    name = _source_name(source)
    if name is not None:
        module.source_name = name
    return module


@dataclass
class OptimizationResult:
    module: Module
    original: Module
    before: Metrics
    after: Metrics
    pipeline: PipelineReport
    level: int
    global_phase: float = 0.0
    verification: object | None = None
    llvm_diagnostic: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_ll(self) -> str:
        return self.module.to_ll()

    def to_bitcode(self) -> bytes:
        return to_bitcode(self.module)

    def estimates(
        self, error_budget: float = 1e-3, qubit_params: str = "qubit_gate_ns_e3"
    ) -> EstimateComparison:
        """Fault-tolerant resource estimate before and after optimization."""
        return compare(
            estimate(self.original, error_budget, qubit_params),
            estimate(self.module, error_budget, qubit_params),
        )

    @property
    def instructions_removed(self) -> int:
        return self.before.total_instructions - self.after.total_instructions

    def to_dict(self) -> dict:
        data = {
            "source": self.module.source_name,
            "level": self.level,
            "global_phase": self.global_phase,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "pipeline": self.pipeline.to_dict(),
            "notes": self.notes,
        }
        if self.verification is not None:
            data["verification"] = self.verification.to_dict()
        if self.llvm_diagnostic is not None:
            data["llvm_diagnostic"] = self.llvm_diagnostic
        return data


def optimize(
    source: Source,
    level: int = 2,
    *,
    passes: list[str] | None = None,
    disable: list[str] | None = None,
    gateset: str = "auto",
    preserve_global_phase: bool = False,
    tolerance: float = 1e-9,
    max_iterations: int | None = None,
    verify: bool = False,
    llvm_check: bool = False,
    time_budget_s: float | None = None,
) -> OptimizationResult:
    """Optimize a QIR module.

    Args:
        source: path, IR text, bitcode bytes, or an already parsed module.
        level: ``-O`` level (0-3).
        passes: run exactly these passes instead of the level's pipeline.
        disable: drop these passes from the pipeline.
        gateset: ``"auto"`` may introduce core gates (S, T, Z, Rz, ...);
            ``"strict"`` only re-emits gates the input module already used.
        preserve_global_phase: refuse rewrites that change the global phase.
        verify: check unitary equivalence against the input (needs numpy).
        llvm_check: run the output through LLVM's verifier (needs PyQIR).
        time_budget_s: stop the *optimization pipeline* early past this many
            seconds instead of running it to a fixed point.  Scoped to the
            pipeline specifically, not the whole call: parsing and (if
            requested) verification happen outside it and are bounded
            separately, by input size and by the reference simulator's own
            qubit-count ceiling respectively, not by wall-clock time.  Every
            individual rewrite already preserves the unitary on its own, so
            a time-limited run can only ever be less optimized, never
            incorrect -- ``result.pipeline`` reports where it stopped, and
            ``result.pipeline.total_rewrites`` is unaffected either way.
            ``None`` (the default) means no limit.
    """
    text = read_source(source)
    module = parse_ll(text)
    original = parse_ll(text)
    name = _source_name(source)
    if name is not None:
        module.source_name = original.source_name = name

    before = measure(original)
    policy = SynthesisPolicy.from_module(
        module,
        mode=gateset,
        preserve_global_phase=preserve_global_phase,
        tol=tolerance,
    )
    ctx = PassContext(policy=policy, tol=tolerance)
    pipeline = build_pipeline(level, only=passes, disable=disable)
    iterations = (
        max_iterations
        if max_iterations is not None
        else MAX_ITERATIONS.get(level, 8) if passes is None else 8
    )
    report = PassManager(pipeline, max_iterations=iterations).run(
        module, ctx, time_budget_s=time_budget_s
    )
    after = measure(module)

    result = OptimizationResult(
        module=module,
        original=original,
        before=before,
        after=after,
        pipeline=report,
        level=level,
        global_phase=module.global_phase,
    )
    if module.global_phase:
        result.notes.append(
            f"output differs from the input by a global phase of "
            f"{module.global_phase:.6f} rad (unobservable)"
        )
    if verify:
        from .verify import verify_equivalence

        result.verification = verify_equivalence(original, module, tol=1e-8)
    if llvm_check:
        result.llvm_diagnostic = llvm_verify(module.to_ll())
    return result
