"""numpy-backed reference simulator.

Same semantics as :mod:`qizil.verify.simulator` — and the same gate matrices —
but fast enough for wider circuits.  Used automatically when numpy is
importable; nothing in Qizil requires it.
"""

from __future__ import annotations

from ..ir.module import QuantumOp
from .matrices import Unsupported, gate_matrix

__all__ = ["MAX_QUBITS", "available", "unitary_of_segment", "compare", "name"]

MAX_QUBITS = 12

name = "numpy"


def available() -> bool:
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _np():
    import numpy as np

    return np


def _embed(np, gate, qubits: list[int], n: int):
    dim = 1 << n
    k = len(qubits)
    full = np.zeros((dim, dim), dtype=complex)
    for col in range(dim):
        sub = 0
        for pos, q in enumerate(qubits):
            sub |= ((col >> q) & 1) << pos
        for row_sub in range(1 << k):
            amp = gate[row_sub, sub]
            if amp == 0:
                continue
            row = col
            for pos, q in enumerate(qubits):
                bit = (row_sub >> pos) & 1
                row = (row & ~(1 << q)) | (bit << q)
            full[row, col] += amp
    return full


def unitary_of_segment(ops: list[QuantumOp], index: dict, num_qubits: int):
    np = _np()
    if num_qubits > MAX_QUBITS:
        raise Unsupported(f"{num_qubits} qubits exceeds the {MAX_QUBITS}-qubit limit")
    total = np.eye(1 << num_qubits, dtype=complex)
    for op in ops:
        matrix = np.array(gate_matrix(op), dtype=complex)
        qubits = [index[q.key] for q in op.qubits]
        total = _embed(np, matrix, qubits, num_qubits) @ total
    return total


def compare(ua, ub) -> tuple[float, float]:
    """``(phase, error)`` for ``U_a == exp(i*phase) * U_b``."""
    np = _np()
    overlap = np.vdot(ub.reshape(-1), ua.reshape(-1))
    if abs(overlap) < 1e-12:
        return 0.0, float(np.abs(ua - ub).max())
    phase = float(np.angle(overlap))
    error = float(np.abs(ua - np.exp(1j * phase) * ub).max())
    return phase, error
