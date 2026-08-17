"""Direct tests of the numpy-accelerated reference simulator.

qizil.verify.accelerated._apply implements "apply a k-qubit gate to a full
n-qubit unitary under construction" via a reshape + tensordot + transpose,
chosen for speed over the far simpler (and, for a while, actually shipped)
alternative of embedding each gate into a full dim x dim matrix and doing a
dense matrix-matrix multiply.  That switch is easy to get wrong -- a
plausible-looking first attempt at it silently swapped the operand order for
every multi-qubit gate, and was only caught by the exhaustive cross-check
below (run against qizil.verify.simulator's independently-implemented dense
embed, which does not share this code path) before it ever reached a commit.
These tests exist so a future change to _apply gets the same scrutiny
without needing to be reconstructed from scratch.
"""

from __future__ import annotations

import itertools
import random
import time

import pytest

np = pytest.importorskip("numpy")

from qizil.verify.accelerated import MAX_QUBITS, _apply  # noqa: E402
from qizil.verify.simulator import _placements  # noqa: E402


def _embed_reference(gate, targets: list[int], n: int):
    """A slow, obviously-correct dense embedding, independent of _apply's
    own reshape/tensordot machinery, used only as a cross-check oracle."""
    dim = 1 << n
    k = len(targets)
    full = np.zeros((dim, dim), dtype=complex)
    for col in range(dim):
        sub = 0
        for pos, q in enumerate(targets):
            sub |= ((col >> q) & 1) << pos
        for row_sub in range(1 << k):
            amp = gate[row_sub, sub]
            if amp == 0:
                continue
            row = col
            for pos, q in enumerate(targets):
                bit = (row_sub >> pos) & 1
                row = (row & ~(1 << q)) | (bit << q)
            full[row, col] += amp
    return full


def _random_unitary(rng, k: int):
    a = rng.normal(size=(2**k, 2**k)) + 1j * rng.normal(size=(2**k, 2**k))
    q, _ = np.linalg.qr(a)
    return q


@pytest.mark.parametrize("n", [2, 3, 4])
def test_apply_matches_a_dense_embedding_for_every_arity_and_ordering(n):
    """Every (arity, target-qubit-and-order) combination that actually
    occurs in this project's gate table (1, 2, and 3-qubit gates), cross-
    checked against an independent dense-embedding oracle -- not against
    accelerated.py's own prior implementation, which is exactly the kind of
    self-comparison that would have missed the operand-order bug."""
    rng = np.random.default_rng(0)
    worst = 0.0
    checked = 0
    for k in (1, 2, 3):
        if k > n:
            continue
        for targets in itertools.permutations(range(n), k):
            gate = _random_unitary(rng, k)
            expected = _embed_reference(gate, list(targets), n) @ np.eye(
                1 << n, dtype=complex
            )
            actual = np.eye(1 << n, dtype=complex)
            _apply(np, actual, gate, list(targets), n)
            worst = max(worst, float(np.abs(expected - actual).max()))
            checked += 1
    assert checked > 0
    assert worst < 1e-10, f"max error {worst:.3e} across {checked} combinations"


def test_apply_composes_correctly_over_a_long_random_sequence():
    """Catches errors that only a *sequence* of applications would expose
    (e.g. a permutation that happens to be its own inverse, which a
    single-gate test cannot distinguish from doing nothing)."""
    rng = np.random.default_rng(1)
    py_rng = random.Random(99)
    for n in (3, 5, 6):
        dim = 1 << n
        expected = np.eye(dim, dtype=complex)
        actual = np.eye(dim, dtype=complex)
        for _ in range(40):
            k = min(py_rng.choice([1, 2, 3]), n)
            targets = py_rng.sample(range(n), k)
            gate = _random_unitary(rng, k)
            expected = _embed_reference(gate, targets, n) @ expected
            _apply(np, actual, gate, targets, n)
        err = float(np.abs(expected - actual).max())
        assert err < 1e-9, f"n={n}: composed error {err:.3e}"


def test_apply_preserves_unitarity():
    rng = np.random.default_rng(2)
    n = 5
    dim = 1 << n
    total = np.eye(dim, dtype=complex)
    for _ in range(20):
        k = random.Random(3).choice([1, 2])
        targets = random.Random(4).sample(range(n), k)
        gate = _random_unitary(rng, k)
        _apply(np, total, gate, targets, n)
    identity_error = float(np.abs(total @ total.conj().T - np.eye(dim)).max())
    assert identity_error < 1e-8


def test_placements_have_target_bits_cleared_and_are_distinct():
    """_placements(n, targets) enumerates the "free" bit patterns a k-qubit
    gate's contraction is broadcast over -- one per group of 2^k basis
    states that differ only in the target qubits. Shared with (and trusted
    from) the pure-Python backend; this is the invariant that makes
    partitioning the basis states into those groups safe."""
    for n in (3, 5):
        for targets in [[0], [1, 2], [0, n - 1]]:
            k = len(targets)
            placements = _placements(n, targets)
            assert len(placements) == (1 << n) // (1 << k)
            assert len(set(placements)) == len(placements)
            mask = sum(1 << q for q in targets)
            assert all(p & mask == 0 for p in placements)


@pytest.mark.parametrize("n,gates,budget_s", [(10, 60, 2.0), (12, 20, 3.0)])
def test_apply_stays_fast_at_realistic_qubit_counts(n, gates, budget_s):
    """A regression guard for the specific defect this module's docstring
    describes in detail: embedding each gate into a full dim x dim matrix
    and multiplying it in is O(dim^3) per gate. At n=12 (dim=4096) a single
    such multiply measured ~5 seconds; a 20-gate segment would take
    roughly two minutes. This bounds the same workload tightly enough that
    a return to that approach fails immediately, on a laptop, in CI.
    """
    if n > MAX_QUBITS:
        pytest.skip(f"n={n} exceeds MAX_QUBITS={MAX_QUBITS}")
    rng = np.random.default_rng(5)
    dim = 1 << n
    total = np.eye(dim, dtype=complex)
    py_rng = random.Random(6)
    ops = []
    for _ in range(gates):
        k = py_rng.choice([1, 2])
        targets = py_rng.sample(range(n), k)
        ops.append((_random_unitary(rng, k), targets))

    t0 = time.perf_counter()
    for gate, targets in ops:
        _apply(np, total, gate, targets, n)
    elapsed = time.perf_counter() - t0

    assert elapsed < budget_s, (
        f"{gates} gates at n={n} took {elapsed:.2f}s, expected under "
        f"{budget_s}s -- check for a reintroduced O(dim^3) per-gate cost"
    )
