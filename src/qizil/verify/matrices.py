"""Reference gate matrices, as plain nested tuples of Python complex numbers.

This is the single source of truth for what each gate *means*.  Both the
dependency-free simulator and the numpy accelerator read their matrices from
here, so the two backends cannot drift apart.
"""

from __future__ import annotations

import cmath
import math

from ..ir.module import QuantumOp

__all__ = ["Matrix", "gate_matrix", "dagger", "rotation", "controlled", "Unsupported"]

Matrix = tuple[tuple[complex, ...], ...]

_S2 = 1.0 / math.sqrt(2.0)


class Unsupported(Exception):
    """The circuit is outside what the reference simulator models."""


FIXED: dict[str, Matrix] = {
    "i": ((1, 0), (0, 1)),
    "id": ((1, 0), (0, 1)),
    "x": ((0, 1), (1, 0)),
    "y": ((0, -1j), (1j, 0)),
    "z": ((1, 0), (0, -1)),
    "h": ((_S2, _S2), (_S2, -_S2)),
    "s": ((1, 0), (0, 1j)),
    "t": ((1, 0), (0, cmath.exp(1j * math.pi / 4))),
}

SWAP: Matrix = (
    (1, 0, 0, 0),
    (0, 0, 1, 0),
    (0, 1, 0, 0),
    (0, 0, 0, 1),
)


def rotation(axis: str, theta: float) -> Matrix:
    """``R_axis(theta) = exp(-i * theta * P_axis / 2)``."""
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    if axis == "X":
        return ((c, -1j * s), (-1j * s, c))
    if axis == "Y":
        return ((c, -s), (s, c))
    return ((complex(c, -s), 0), (0, complex(c, s)))


def pair_rotation(axis: str, theta: float) -> Matrix:
    """``exp(-i * theta * (P (x) P) / 2)``."""
    p = FIXED[axis.lower()]
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    rows = []
    for i in range(4):
        row = []
        for j in range(4):
            pp = p[i >> 1][j >> 1] * p[i & 1][j & 1]
            row.append((c if i == j else 0) - 1j * s * pp)
        rows.append(tuple(row))
    return tuple(rows)


def controlled(u: Matrix, num_controls: int) -> Matrix:
    """Controls occupy the low bits of the sub-index, the target the high bit."""
    dim = 1 << (num_controls + 1)
    mask = (1 << num_controls) - 1
    active = [i for i in range(dim) if (i & mask) == mask]
    rows = [[complex(1 if i == j else 0) for j in range(dim)] for i in range(dim)]
    for a, ia in enumerate(active):
        for b, ib in enumerate(active):
            rows[ia][ib] = complex(u[a][b])
    return tuple(tuple(r) for r in rows)


def dagger(m: Matrix) -> Matrix:
    return tuple(
        tuple(complex(m[j][i]).conjugate() for j in range(len(m))) for i in range(len(m))
    )


def gate_matrix(op: QuantumOp) -> Matrix:
    """Matrix for one gate; operand 0 is the least significant sub-index bit."""
    spec = op.spec
    if spec.kind != "gate":
        raise Unsupported(f"{op.base} is not a unitary gate")
    base = op.base

    if base in FIXED:
        m = FIXED[base]
    elif spec.rot_axis is not None:
        angle = op.angles[0] if op.angles else None
        if angle is None:
            raise Unsupported(f"{base} has a symbolic angle")
        m = rotation(spec.rot_axis, angle)
    elif spec.pair_axis is not None:
        angle = op.angles[0] if op.angles else None
        if angle is None:
            raise Unsupported(f"{base} has a symbolic angle")
        m = pair_rotation(spec.pair_axis, angle)
    elif base in ("cnot", "cx"):
        m = controlled(FIXED["x"], 1)
    elif base == "cy":
        m = controlled(FIXED["y"], 1)
    elif base == "cz":
        m = controlled(FIXED["z"], 1)
    elif base == "ccx":
        m = controlled(FIXED["x"], 2)
    elif base == "swap":
        m = SWAP
    else:
        raise Unsupported(f"no reference matrix for gate {base}")

    m = tuple(tuple(complex(v) for v in row) for row in m)
    if op.functor == "adj":
        return dagger(m)
    if op.functor != "body":
        raise Unsupported(f"functor {op.functor} is not simulated")
    return m
