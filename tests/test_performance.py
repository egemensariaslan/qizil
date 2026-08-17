"""Performance regression guards.

Not a benchmark suite — a tripwire. Each test bounds wall-clock time for a
circuit size that should be comfortably fast; a regression to quadratic (or
worse) behavior in any pass blows through these bounds by an order of
magnitude, which is what actually catches it in CI.

The bound this file exists to prevent regressing: qizil.passes.cliffordt once
restarted its scan from the top of the block after every single rewrite,
making it cubic in the number of rewrites for long blocks. A 5000-gate random
circuit went from 15.3s to 0.7s when that pass was rewritten as a single
left-to-right sweep (see git history on cliffordt.py). These thresholds are
set well above current measured times so they do not flake on a slow CI
runner, while still catching a return to superlinear blowup.
"""

from __future__ import annotations

import random
import time

import pytest

from conftest import make_ir, random_gates
from qizil.api import optimize


@pytest.mark.parametrize("n_gates,n_qubits,budget_s", [
    (1000, 10, 2.0),
    (5000, 16, 5.0),
])
def test_optimization_stays_fast(n_gates, n_qubits, budget_s):
    rng = random.Random(2026)
    gates = random_gates(rng, n_gates, n_qubits)
    ir = make_ir(gates, n_qubits)

    t0 = time.perf_counter()
    optimize(ir, level=3)
    elapsed = time.perf_counter() - t0

    assert elapsed < budget_s, (
        f"optimizing a {n_gates}-gate circuit took {elapsed:.2f}s, "
        f"expected under {budget_s}s — check for a reintroduced O(n^2)+ pass"
    )


def test_time_grows_no_worse_than_roughly_linear():
    """A 5x larger circuit should not take more than ~15x longer.

    True O(n) would give 5x; this leaves headroom for the pipeline's
    fixed-point iteration and the passes' own mild superlinear terms
    (see docs/BENCHMARKS.md) without masking a return to quadratic-plus
    blowup, which would show up as 20x-100x+ on this ratio.
    """
    rng = random.Random(7)
    small = make_ir(random_gates(rng, 800, 12), 12)
    large = make_ir(random_gates(rng, 4000, 12), 12)

    t0 = time.perf_counter()
    optimize(small, level=3)
    small_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    optimize(large, level=3)
    large_time = time.perf_counter() - t0

    ratio = large_time / max(small_time, 1e-6)
    assert ratio < 15, (
        f"5x more gates took {ratio:.1f}x longer ({small_time*1000:.0f}ms -> "
        f"{large_time*1000:.0f}ms) — looks superlinear again"
    )
