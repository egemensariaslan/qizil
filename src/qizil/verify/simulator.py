"""Dependency-free reference simulator.

Builds the unitary of a gate run using nothing but Python complex numbers, so
the equivalence check — the evidence that a rewrite is correct — is available
in a bare clone with no packages installed.

The unitary is held as its 2^n columns and each gate is applied the way a
statevector simulator applies it: only the amplitudes whose target bits differ
are touched.  That makes one gate cost ``2^n * 2^k`` complex multiplications
instead of the ``2^3n`` of a naive matrix product, which is what keeps a
pure-Python check practical up to :data:`MAX_QUBITS` qubits.  Beyond that the
numpy backend takes over (see :mod:`qizil.verify.accelerated`).
"""

from __future__ import annotations

import cmath

from ..ir.module import QuantumOp
from .matrices import Matrix, Unsupported, gate_matrix

__all__ = ["MAX_QUBITS", "unitary_of_segment", "compare", "name"]

#: Above this the pure-Python path gets slow enough to be a bad experience.
MAX_QUBITS = 8

name = "python"


def _placements(num_qubits: int, targets: list[int]) -> list[int]:
    """Every basis index with the target bits cleared."""
    free = [b for b in range(num_qubits) if b not in targets]
    out = [0]
    for bit in free:
        mask = 1 << bit
        out += [value | mask for value in out]
    return out


def _offsets(targets: list[int]) -> list[int]:
    """Basis offset contributed by each sub-index value of the target bits."""
    out = [0]
    for bit in targets:
        mask = 1 << bit
        out = out + [value | mask for value in out]
    # `out` is built least-significant target first, matching the sub-index
    # convention: operand 0 is bit 0 of the sub-index.
    return out


def _apply(column: list[complex], matrix: Matrix, targets: list[int], n: int) -> None:
    """In-place ``column <- (matrix on targets) @ column``."""
    size = len(matrix)
    offsets = _offsets(targets)
    for base in _placements(n, targets):
        idx = [base + off for off in offsets]
        amps = [column[i] for i in idx]
        for r in range(size):
            row = matrix[r]
            total = 0j
            for c in range(size):
                factor = row[c]
                if factor:
                    total += factor * amps[c]
            column[idx[r]] = total


def unitary_of_segment(
    ops: list[QuantumOp], index: dict, num_qubits: int
) -> list[list[complex]]:
    """Unitary of a run of gates, as a list of columns."""
    if num_qubits > MAX_QUBITS:
        raise Unsupported(
            f"{num_qubits} qubits exceeds the {MAX_QUBITS}-qubit limit of the "
            "dependency-free simulator (install numpy for up to 12)"
        )
    dim = 1 << num_qubits
    columns = [[1j * 0 if i != j else 1 + 0j for i in range(dim)] for j in range(dim)]
    for op in ops:
        matrix = gate_matrix(op)
        targets = [index[q.key] for q in op.qubits]
        for column in columns:
            _apply(column, matrix, targets, num_qubits)
    return columns


def compare(ua, ub) -> tuple[float, float]:
    """``(phase, error)`` for ``U_a == exp(i*phase) * U_b``."""
    overlap = 0j
    for col_a, col_b in zip(ua, ub):
        for a, b in zip(col_a, col_b):
            overlap += b.conjugate() * a
    if abs(overlap) < 1e-12:
        error = max(
            abs(a - b) for col_a, col_b in zip(ua, ub) for a, b in zip(col_a, col_b)
        )
        return 0.0, float(error)
    phase = cmath.phase(overlap)
    factor = cmath.exp(1j * phase)
    error = max(
        abs(a - factor * b)
        for col_a, col_b in zip(ua, ub)
        for a, b in zip(col_a, col_b)
    )
    return float(phase), float(error)
