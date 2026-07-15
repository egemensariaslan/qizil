"""The property that matters: U_out == U_in for every rewrite we make.

Random circuits are optimized at every level and checked against a reference
simulator, which is the only way to be confident about a table of gate
identities.  Global phase is checked too: the phase Qizil claims to have
dropped has to be the phase the matrices actually differ by.
"""

from __future__ import annotations

import math

import pytest

from conftest import make_ir, random_gates
from qizil.api import optimize
from qizil.ir.parser import parse_ll
from qizil.verify import verify_equivalence

pytest.importorskip("numpy")

SEEDS = list(range(40))


def _phase_close(a: float, b: float) -> bool:
    return abs(math.remainder(a - b, 2 * math.pi)) < 1e-8


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("level", [1, 2, 3])
def test_random_circuits_preserve_the_unitary(seed, level):
    import random

    rng = random.Random(seed)
    num_qubits = rng.choice([2, 3])
    gates = random_gates(rng, 24, num_qubits)
    ir = make_ir(gates, num_qubits)

    result = optimize(ir, level=level, verify=True)
    assert result.verification.ok, result.verification.messages
    assert result.verification.checked_segments >= 1
    assert _phase_close(result.global_phase, result.verification.global_phase)


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_preserve_global_phase_mode_keeps_the_phase(seed):
    import random

    rng = random.Random(1000 + seed)
    gates = random_gates(rng, 24, 3)
    result = optimize(
        make_ir(gates, 3), level=3, preserve_global_phase=True, verify=True
    )
    assert result.verification.ok, result.verification.messages
    assert result.global_phase == 0.0
    assert abs(result.verification.global_phase) < 1e-9


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_optimization_is_idempotent(seed):
    import random

    rng = random.Random(2000 + seed)
    gates = random_gates(rng, 24, 3)
    once = optimize(make_ir(gates, 3), level=3)
    twice = optimize(once.to_ll(), level=3)
    assert twice.to_ll() == once.to_ll()
    assert twice.pipeline.total_rewrites == 0


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_output_reparses_and_is_stable(seed):
    import random

    rng = random.Random(3000 + seed)
    gates = random_gates(rng, 20, 3)
    result = optimize(make_ir(gates, 3), level=3)
    reparsed = parse_ll(result.to_ll())
    assert reparsed.to_ll() == result.to_ll()


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_output_passes_the_llvm_verifier(seed):
    pytest.importorskip("pyqir")
    import random

    rng = random.Random(4000 + seed)
    gates = random_gates(rng, 20, 3)
    result = optimize(make_ir(gates, 3, measure=True), level=3, llvm_check=True)
    assert result.llvm_diagnostic is None


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize(
    "name",
    [
        "bell_redundant.ll",
        "commuting_t.ll",
        "adaptive_branch.ll",
        "dynamic_qubits.ll",
        "trotter_step.ll",
    ],
)
def test_shipped_examples_verify(name, level):
    import os

    from conftest import EXAMPLES

    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        text = fh.read()
    result = optimize(text, level=level, verify=True)
    assert result.verification.ok, result.verification.messages
    assert _phase_close(result.global_phase, result.verification.global_phase)


def test_verifier_catches_a_deliberately_wrong_rewrite():
    original = parse_ll(make_ir([("t", "body", None, [0])] * 2, 1))
    broken = parse_ll(make_ir([("t", "body", None, [0])], 1))
    result = verify_equivalence(original, broken)
    assert not result.ok


def test_verifier_catches_reordered_measurements():
    original = parse_ll(
        make_ir([("h", "body", None, [0]), ("h", "body", None, [1])], 2, measure=True)
    )
    swapped_text = make_ir(
        [("h", "body", None, [0]), ("h", "body", None, [1])], 2, measure=True
    ).replace(
        "  call void @__quantum__qis__mz__body(%Qubit* null, %Result* null)\n", ""
    )
    result = verify_equivalence(original, parse_ll(swapped_text))
    assert not result.ok
