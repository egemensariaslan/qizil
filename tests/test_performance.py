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


# --------------------------------------------------------------------------
# time_budget_s: the pipeline must terminate, and stay correct, on schedule
# --------------------------------------------------------------------------


def _adversarial_timing_ir():
    """Many qubits, sparse overlap: the regime that makes the ``commute``
    pass's forward scan run long (see docs/BENCHMARKS.md). Too many qubits
    to run through the reference simulator -- use only for timing."""
    rng = random.Random(7)
    gates = random_gates(rng, 4000, 250)
    return make_ir(gates, 250)


def _adversarial_verifiable_ir():
    """The same sparse-overlap regime, small enough (qubits and gate count)
    to stay inside the reference simulator's practical range so truncated
    output can still be independently re-verified end to end. Dense unitary
    verification is inherently O(dim^2) per gate at best (see
    docs/BENCHMARKS.md) -- this is sized to actually finish, not to stress
    the pipeline's own timing."""
    rng = random.Random(8)
    gates = random_gates(rng, 150, 8)
    return make_ir(gates, 8)


def test_time_budget_bounds_wall_clock_on_an_adversarial_circuit():
    ir = _adversarial_timing_ir()

    t0 = time.perf_counter()
    optimize(ir, level=3, time_budget_s=1.0)
    elapsed = time.perf_counter() - t0

    # Generous multiplier: parsing this large a module happens outside the
    # budgeted region (see qizil.api.optimize's time_budget_s docstring), so
    # this bounds the whole call, not just the pipeline it directly limits.
    assert elapsed < 5.0, f"took {elapsed:.2f}s with a 1.0s pipeline budget"


def test_time_budget_result_is_still_fully_correct():
    """A truncated pipeline must never be wrong -- only less optimized."""
    from qizil.verify import verify_equivalence

    ir = _adversarial_verifiable_ir()
    full = optimize(ir, level=3)
    truncated = optimize(ir, level=3, time_budget_s=0.01)

    assert truncated.after.gates >= full.after.gates, (
        "a time-limited run should never out-optimize the untimed run"
    )
    # Whatever qizil actually produced, however much it got done, must still
    # be exactly equivalent to the input -- re-verify from scratch rather
    # than trust the truncated run's own bookkeeping.
    check = verify_equivalence(truncated.original, truncated.module)
    assert check.available, check.messages
    assert check.ok, check.messages


def test_time_budget_reports_where_it_stopped():
    ir = _adversarial_timing_ir()
    result = optimize(ir, level=3, time_budget_s=0.05)
    notes = [n for s in result.pipeline.per_pass.values() for n in s.notes]
    assert any("time budget" in n for n in notes)


def test_no_time_budget_means_no_limit():
    """The default must reproduce the exact pre-existing behavior."""
    ir = make_ir(random_gates(random.Random(3), 400, 8), 8)
    default = optimize(ir, level=3)
    explicit_none = optimize(ir, level=3, time_budget_s=None)
    assert default.to_ll() == explicit_none.to_ll()


def test_generous_time_budget_does_not_truncate_a_normal_circuit():
    ir = make_ir(random_gates(random.Random(4), 500, 8), 8)
    unbounded = optimize(ir, level=3)
    generously_bounded = optimize(ir, level=3, time_budget_s=30.0)
    assert unbounded.to_ll() == generously_bounded.to_ll()
    notes = [
        n
        for s in generously_bounded.pipeline.per_pass.values()
        for n in s.notes
        if "time budget" in n
    ]
    assert notes == []
