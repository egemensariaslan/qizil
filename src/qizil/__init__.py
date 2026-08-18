"""Qizil (QIR-Opt) - a peephole and commutation optimizer for QIR.

Ingests QIR (``.ll`` or ``.bc``), rewrites the quantum instructions through a
dependency DAG, and emits optimized QIR with classical control flow untouched.
"""

from .analysis import Estimate, Metrics, estimate, measure
from .api import OptimizationResult, optimize, parse, read_source
from .ir import Module, ParseError, parse_file, parse_ll
from .passes import PASS_REGISTRY, PIPELINES, PassManager, build_pipeline

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "optimize",
    "OptimizationResult",
    "parse",
    "parse_ll",
    "parse_file",
    "read_source",
    "Module",
    "ParseError",
    "measure",
    "Metrics",
    "estimate",
    "Estimate",
    "PassManager",
    "build_pipeline",
    "PASS_REGISTRY",
    "PIPELINES",
]
