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
        "qft_roundtrip.ll",
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


def test_qft_then_inverse_qft_collapses_to_the_identity():
    """QFT_n . QFT_n^-1 = I is known in closed form -- not a claim about the
    optimizer, a claim about the mathematics -- so this is the strongest
    correctness check in the suite: it fails if the algebra is wrong, not
    just if it regresses.
    """
    import os

    from conftest import EXAMPLES

    with open(os.path.join(EXAMPLES, "qft_roundtrip.ll"), encoding="utf-8") as fh:
        text = fh.read()
    result = optimize(text, level=3, verify=True)
    assert result.verification.ok, result.verification.messages
    assert result.after.gates == 0, "QFT . QFT^-1 should optimize away entirely"
    assert result.global_phase == 0.0, "this construction carries no global phase"


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


# --------------------------------------------------------------------------
# Backend agreement: the dependency-free simulator must match numpy exactly
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS[:20])
def test_backends_agree(seed):
    import random

    from qizil.verify import accelerated, simulator

    if not accelerated.available():
        pytest.skip("numpy not installed")

    rng = random.Random(5000 + seed)
    num_qubits = rng.choice([2, 3])
    gates = random_gates(rng, 18, num_qubits)
    module = parse_ll(make_ir(gates, num_qubits))
    ops = [
        inst.op
        for _fn, block in module.blocks()
        for inst in block.instructions
        if inst.op is not None and inst.op.is_gate
    ]
    keys = sorted({q.key for op in ops for q in op.qubits}, key=repr)
    index = {k: i for i, k in enumerate(keys)}
    n = max(1, len(index))

    pure = simulator.unitary_of_segment(ops, index, n)
    fast = accelerated.unitary_of_segment(ops, index, n)
    worst = max(
        abs(pure[j][i] - fast[i][j]) for i in range(1 << n) for j in range(1 << n)
    )
    assert worst < 1e-12, worst


def test_pure_backend_verifies_without_numpy(monkeypatch):
    from qizil.verify import accelerated

    monkeypatch.setattr(accelerated, "available", lambda: False)
    result = optimize(
        make_ir([("t", "body", None, [0])] * 8 + [("h", "body", None, [1])] * 2, 2),
        level=3,
        verify=True,
    )
    assert result.verification.backend == "python"
    assert result.verification.ok, result.verification.messages
    assert result.after.gates == 0
