"""Randomized equivalence checking against the dependency-free simulator.

tests/test_semantics.py has ~220 tests but starts with a module-level
``pytest.importorskip("numpy")`` -- when numpy is absent, pytest doesn't
individually skip those tests, it fails to *collect* the whole module (one
skip event, not 220), so `pytest -q` in a bare environment quietly reports
"skipped" for the entire randomized-equivalence proof and moves on. Worse:
even when numpy *is* installed, qizil.verify.select_backend() always prefers
it, so that whole suite has never actually exercised the pure-Python
simulator in qizil.verify.simulator -- the one that actually ships as the
zero-dependency default and is the one `./qizil ... --verify` runs on a bare
clone.

This file has no numpy gate. It forces the pure-Python backend (the same way
test_semantics.py's own test_pure_backend_verifies_without_numpy does, for
one fixed circuit) and runs it across many random circuits and every
optimization level, so that simulator gets the same kind of fuzz coverage as
the numpy path, in every environment, always visibly collected.

Circuit sizes are smaller than test_semantics.py's: the pure-Python backend
embeds each gate as a dense 2^n x 2^n operation (see simulator.py's module
docstring), so it is slower per-qubit than numpy, and MAX_QUBITS there is 8.
"""

from __future__ import annotations

import math
import random

import pytest

from conftest import make_ir, random_gates
from qizil.api import optimize
from qizil.verify import accelerated

SEEDS = list(range(25))


def _phase_close(a: float, b: float) -> bool:
    return abs(math.remainder(a - b, 2 * math.pi)) < 1e-8


@pytest.fixture(autouse=True)
def force_pure_python_backend(monkeypatch):
    """Make qizil.verify.select_backend() pick simulator.py even if numpy
    happens to be installed in this environment, so this file's coverage
    doesn't silently degrade into re-testing the numpy path."""
    monkeypatch.setattr(accelerated, "available", lambda: False)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("level", [1, 2, 3])
def test_random_circuits_verify_on_the_pure_python_simulator(seed, level):
    rng = random.Random(10_000 + seed)
    num_qubits = rng.choice([2, 3])
    gates = random_gates(rng, 18, num_qubits)
    ir = make_ir(gates, num_qubits)

    result = optimize(ir, level=level, verify=True)
    assert result.verification.backend == "python"
    assert result.verification.ok, result.verification.messages
    assert result.verification.checked_segments >= 1
    assert _phase_close(result.global_phase, result.verification.global_phase)


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_preserve_global_phase_on_the_pure_python_simulator(seed):
    rng = random.Random(11_000 + seed)
    gates = random_gates(rng, 18, 3)
    result = optimize(
        make_ir(gates, 3), level=3, preserve_global_phase=True, verify=True
    )
    assert result.verification.backend == "python"
    assert result.verification.ok, result.verification.messages
    assert result.global_phase == 0.0
    assert abs(result.verification.global_phase) < 1e-9


@pytest.mark.parametrize(
    "name",
    [
        "bell_redundant.ll",
        "commuting_t.ll",
        "adaptive_branch.ll",
        "dynamic_qubits.ll",
        "qft_roundtrip.ll",
        "trotter_step.ll",
    ],
)
def test_shipped_examples_verify_on_the_pure_python_simulator(name):
    import os

    from conftest import EXAMPLES

    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        text = fh.read()
    result = optimize(text, level=3, verify=True)
    assert result.verification.backend == "python"
    assert result.verification.ok, result.verification.messages
