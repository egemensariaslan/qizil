"""numpy-backed reference simulator.

Same semantics as :mod:`qizil.verify.simulator` (same gate matrices) but
applying each gate to the *whole* running unitary in one vectorized numpy
call instead of walking it one column, one placement group, one complex
multiply at a time in a Python loop.

The technique -- standard for local-operator application inside a larger
statevector simulation -- is to reshape the flat ``dim x dim`` unitary under
construction into an ``n``-qubit tensor of shape ``(2,) * n + (dim,)`` (one
size-2 axis per qubit on the "row" side, plus the untouched "column" side),
then contract the gate matrix against only the axes it acts on with
``numpy.tensordot``.  That touches the full array once per gate via a
BLAS-backed contraction: real cost ``O(dim^2)`` element-touches per gate
regardless of arity ``k``, which is the best achievable since a gate can in
principle change every entry of a ``dim x dim`` matrix.

Two earlier approaches (kept here as a record, since both were individually
plausible and each was empirically ~2-3 orders of magnitude slower on a
12-qubit, ~100-gate segment than the one below):

* Embedding the gate into a full ``dim x dim`` matrix and multiplying it
  into the running product (``total = embed(gate) @ total``) is a genuine
  ``O(dim^3)`` matrix-matrix product *per gate* -- cubic, not quadratic.
  At 12 qubits (dim=4096) a single such multiply measured ~5 seconds.
* Selecting rows via numpy fancy indexing (``total[rows] = matrix @
  total[rows]``) looks like the same local update, but fancy indexing
  always *copies*: it materializes a fresh, full ``dim x dim``-sized array
  on the read and another on the write-back, through a scattered
  (non-contiguous) access pattern.  Right complexity class, wrong
  constant -- empirically dramatically slower than a contiguous,
  BLAS-friendly contraction at the same size.

The bit-ordering trap that made the *first* correct-looking rewrite of this
function wrong (caught by an exhaustive cross-check against
qizil.verify.simulator over every gate arity and target-qubit ordering,
before it ever reached the test suite -- see git history): ``.reshape``
decomposes a flat index MSB-first, so a reshaped ``(2^k, 2^k)`` gate
matrix's axis ``i`` corresponds to operand ``k-1-i``, i.e. ``targets[::-1]``
-- not ``targets`` in the order they're given, which is the natural but
wrong assumption. ``target_axis`` below is deliberately reversed to account
for exactly this.
"""

from __future__ import annotations

from ..ir.module import QuantumOp
from .matrices import Unsupported, gate_matrix

__all__ = ["MAX_QUBITS", "available", "unitary_of_segment", "compare", "name"]

MAX_QUBITS = 12

#: Coarse guard against a segment that is *within* MAX_QUBITS but has enough
#: un-fused gates to still take an unreasonable amount of wall-clock time.
#: Building the unitary is inherently O(dim^2) element-touches per gate at
#: best (see this module's docstring) -- there is no implementation trick
#: that makes a 12-qubit, thousand-gate segment fast, so past a point the
#: right behavior is to decline (available=False, with a clear reason)
#: rather than block the caller for minutes. Calibrated with a safety
#: margin below the ~6e-6 ms/unit measured for dim^2 * gate_count at 12
#: qubits, so this triggers noticeably before the estimate above would
#: actually be reached.
_MAX_COST_UNITS = 2_000_000_000  # dim^2 * num_gates

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


def _apply(np, total, matrix, targets: list[int], n: int) -> None:
    """In-place: apply a ``k``-qubit gate (on ``targets``) to every column of
    ``total`` (an ``n``-qubit unitary under construction) at once.

    ``targets`` follows this project's usual convention: ``targets[0]`` is
    operand 0, the least-significant bit of the gate's own sub-index (see
    ``qizil.verify.simulator``'s module docstring / ``_offsets``). ``total``
    is reshaped (a view, no copy) into ``(2,)*n + (dim,)``: qubit ``q``
    lives at tensor axis ``n-1-q`` (row-major reshape puts the
    most-significant bit -- highest qubit index -- first), and the trailing
    axis is the untouched column index.
    """
    dim = total.shape[0]
    k = len(targets)
    tensor = total.reshape((2,) * n + (dim,))
    gate = matrix.reshape((2,) * k + (2,) * k)  # (out legs..., in legs...)

    # Reversed for the same reason gate's own reshape is MSB-first: gate
    # axis i (whichever leg group) is operand k-1-i, i.e. targets[::-1][i].
    target_axis = [n - 1 - q for q in targets][::-1]

    contracted = np.tensordot(gate, tensor, axes=(list(range(k, 2 * k)), target_axis))
    # contracted's axes are now: gate's k leftover ("out") legs, in order,
    # followed by tensor's leftover axes in their original relative order.
    # Restore the qubit-indexed layout with one explicit, unambiguous
    # permutation (np.moveaxis's multi-axis semantics are surprisingly easy
    # to get wrong here -- an earlier version of this function did, and an
    # exhaustive cross-check against the pure-Python backend caught it).
    remaining = [a for a in range(n + 1) if a not in target_axis]
    current_axis_of = list(target_axis) + remaining
    permutation = [current_axis_of.index(final_axis) for final_axis in range(n + 1)]

    total[...] = np.transpose(contracted, permutation).reshape(dim, dim)


def unitary_of_segment(ops: list[QuantumOp], index: dict, num_qubits: int):
    np = _np()
    if num_qubits > MAX_QUBITS:
        raise Unsupported(f"{num_qubits} qubits exceeds the {MAX_QUBITS}-qubit limit")
    dim = 1 << num_qubits
    cost = dim * dim * max(1, len(ops))
    if cost > _MAX_COST_UNITS:
        raise Unsupported(
            f"segment too large to verify in reasonable time "
            f"({len(ops)} gates at {num_qubits} qubits) -- this is a coarse "
            f"heuristic (see accelerated.py), not a hard correctness limit"
        )
    total = np.eye(dim, dtype=complex)
    for op in ops:
        matrix = np.array(gate_matrix(op), dtype=complex)
        targets = [index[q.key] for q in op.qubits]
        _apply(np, total, matrix, targets, num_qubits)
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
