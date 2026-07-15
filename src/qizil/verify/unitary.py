"""Exact equivalence checking: U_out == U_in (up to a reported global phase).

The check is structural *and* numeric.  Both modules are split into segments at
every instruction that is not a unitary gate — measurements, resets, runtime
calls, classical code, terminators.  Those segments must line up textually, and
each run of gates between them must produce the same unitary matrix.  That way
a rewrite can never hide a change in measurement order or control flow behind a
matching matrix.

Requires numpy (``pip install 'qizil[verify]'``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..ir.module import InstKind, Instruction, Module, QuantumOp

__all__ = ["VerifyResult", "verify_equivalence", "unitary_of_segment", "MAX_QUBITS"]

MAX_QUBITS = 12


class Unsupported(Exception):
    """The circuit is outside what the reference simulator models."""


@dataclass
class VerifyResult:
    ok: bool
    checked_segments: int = 0
    skipped_segments: int = 0
    max_error: float = 0.0
    global_phase: float = 0.0
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "equivalent": self.ok,
            "segments_checked": self.checked_segments,
            "segments_skipped": self.skipped_segments,
            "max_error": self.max_error,
            "global_phase": self.global_phase,
            "messages": self.messages,
        }


# --------------------------------------------------------------------------
# Gate matrices
# --------------------------------------------------------------------------


def _np():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise Unsupported(
            "numpy is required for verification: pip install 'qizil[verify]'"
        ) from exc
    return np


def _single_qubit_matrices(np):
    s2 = 1 / math.sqrt(2)
    return {
        "i": np.eye(2, dtype=complex),
        "id": np.eye(2, dtype=complex),
        "x": np.array([[0, 1], [1, 0]], dtype=complex),
        "y": np.array([[0, -1j], [1j, 0]], dtype=complex),
        "z": np.array([[1, 0], [0, -1]], dtype=complex),
        "h": np.array([[s2, s2], [s2, -s2]], dtype=complex),
        "s": np.array([[1, 0], [0, 1j]], dtype=complex),
        "t": np.array([[1, 0], [0, np.exp(1j * math.pi / 4)]], dtype=complex),
    }


def _rotation(np, axis: str, theta: float):
    c, s = math.cos(theta / 2), math.sin(theta / 2)
    if axis == "X":
        return np.array([[c, -1j * s], [-1j * s, c]], dtype=complex)
    if axis == "Y":
        return np.array([[c, -s], [s, c]], dtype=complex)
    return np.array(
        [[complex(c, -s), 0], [0, complex(c, s)]], dtype=complex
    )


def _controlled(np, u, num_controls: int):
    """Controls occupy the low bits of the sub-index, the target the high bit."""
    dim = 1 << (num_controls + 1)
    m = np.eye(dim, dtype=complex)
    mask = (1 << num_controls) - 1
    rows = [i for i in range(dim) if (i & mask) == mask]
    for a, ia in enumerate(rows):
        for b, ib in enumerate(rows):
            m[ia, ib] = u[a, b]
    return m


def _pair_rotation(np, axis: str, theta: float):
    paulis = _single_qubit_matrices(np)
    p = paulis[axis.lower()]
    pp = np.kron(p, p)
    return math.cos(theta / 2) * np.eye(4, dtype=complex) - 1j * math.sin(theta / 2) * pp


def gate_matrix(op: QuantumOp):
    """Matrix for one gate, with operand 0 as the least significant sub-index bit."""
    np = _np()
    spec = op.spec
    if spec.kind != "gate":
        raise Unsupported(f"{op.base} is not a unitary gate")
    base = op.base
    fixed = _single_qubit_matrices(np)

    if base in fixed:
        m = fixed[base]
    elif spec.rot_axis is not None:
        angle = op.angles[0]
        if angle is None:
            raise Unsupported(f"{base} has a symbolic angle")
        m = _rotation(np, spec.rot_axis, angle)
    elif spec.pair_axis is not None:
        angle = op.angles[0]
        if angle is None:
            raise Unsupported(f"{base} has a symbolic angle")
        m = _pair_rotation(np, spec.pair_axis, angle)
    elif base in ("cnot", "cx"):
        m = _controlled(np, fixed["x"], 1)
    elif base == "cy":
        m = _controlled(np, fixed["y"], 1)
    elif base == "cz":
        m = _controlled(np, fixed["z"], 1)
    elif base == "ccx":
        m = _controlled(np, fixed["x"], 2)
    elif base == "swap":
        m = np.array(
            [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=complex
        )
    else:
        raise Unsupported(f"no reference matrix for gate {base}")

    if op.functor == "adj":
        m = m.conj().T
    elif op.functor != "body":
        raise Unsupported(f"functor {op.functor} is not simulated")
    return m


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
    """Unitary of a run of gates over ``num_qubits`` qubits."""
    np = _np()
    if num_qubits > MAX_QUBITS:
        raise Unsupported(f"{num_qubits} qubits exceeds the {MAX_QUBITS}-qubit limit")
    total = np.eye(1 << num_qubits, dtype=complex)
    for op in ops:
        qubits = [index[q.key] for q in op.qubits]
        total = _embed(np, gate_matrix(op), qubits, num_qubits) @ total
    return total


# --------------------------------------------------------------------------
# Module comparison
# --------------------------------------------------------------------------


def _segments(block) -> list[tuple[list[QuantumOp], list[Instruction]]]:
    """Split a block into (gate run, following non-gate instructions) pairs."""
    out: list[tuple[list[QuantumOp], list[Instruction]]] = []
    gates: list[QuantumOp] = []
    fence: list[Instruction] = []
    for inst in block.instructions:
        if inst.kind is InstKind.TRIVIA:
            continue
        is_gate = (
            inst.kind is InstKind.QUANTUM and inst.op is not None and inst.op.is_gate
        )
        if is_gate:
            if fence:
                out.append((gates, fence))
                gates, fence = [], []
            gates.append(inst.op)
        else:
            fence.append(inst)
    out.append((gates, fence))
    return out


def _qubit_index(modules: list[Module]) -> dict:
    keys: set = set()
    for module in modules:
        for _fn, block in module.blocks():
            for inst in block.instructions:
                if inst.kind is InstKind.QUANTUM and inst.op is not None:
                    for q in inst.op.qubits:
                        if not q.is_definite():
                            raise Unsupported("module contains dynamic qubit operands")
                        keys.add(q.key)
    return {k: i for i, k in enumerate(sorted(keys, key=repr))}


def _fence_signature(fence: list[Instruction]) -> list[str]:
    return [" ".join(inst.text.split()) for inst in fence]


def verify_equivalence(
    original: Module, optimized: Module, tol: float = 1e-8
) -> VerifyResult:
    """Check that ``optimized`` implements the same unitary as ``original``."""
    result = VerifyResult(ok=True)
    try:
        np = _np()
        index = _qubit_index([original, optimized])
    except Unsupported as exc:
        return VerifyResult(ok=False, messages=[f"unsupported: {exc}"])
    num_qubits = max(1, len(index))
    if num_qubits > MAX_QUBITS:
        return VerifyResult(
            ok=False,
            skipped_segments=1,
            messages=[f"{num_qubits} qubits exceeds the {MAX_QUBITS}-qubit limit"],
        )

    fa = {f.name: f for f in original.functions}
    fb = {f.name: f for f in optimized.functions}
    if set(fa) != set(fb):
        result.ok = False
        result.messages.append("function sets differ")
        return result

    total_phase = 0.0
    for name, func_a in fa.items():
        func_b = fb[name]
        if len(func_a.blocks) != len(func_b.blocks):
            result.ok = False
            result.messages.append(f"@{name}: basic block count changed")
            continue
        for block_a, block_b in zip(func_a.blocks, func_b.blocks):
            if block_a.label != block_b.label:
                result.ok = False
                result.messages.append(
                    f"@{name}: block label {block_a.label} != {block_b.label}"
                )
                continue
            segs_a, segs_b = _segments(block_a), _segments(block_b)
            if len(segs_a) != len(segs_b):
                result.ok = False
                result.messages.append(
                    f"@{name}/{block_a.label}: non-unitary instruction count changed"
                )
                continue
            for (ops_a, fence_a), (ops_b, fence_b) in zip(segs_a, segs_b):
                if _fence_signature(fence_a) != _fence_signature(fence_b):
                    result.ok = False
                    result.messages.append(
                        f"@{name}/{block_a.label}: non-unitary instructions differ"
                    )
                    continue
                try:
                    ua = unitary_of_segment(ops_a, index, num_qubits)
                    ub = unitary_of_segment(ops_b, index, num_qubits)
                except Unsupported as exc:
                    result.skipped_segments += 1
                    result.messages.append(f"skipped segment: {exc}")
                    continue
                phase, error = _compare(np, ua, ub)
                result.checked_segments += 1
                result.max_error = max(result.max_error, error)
                total_phase += phase
                if error > tol:
                    result.ok = False
                    result.messages.append(
                        f"@{name}/{block_a.label}: unitary differs (error {error:.3e})"
                    )
    result.global_phase = _wrap(total_phase)
    return result


def _compare(np, ua, ub) -> tuple[float, float]:
    """Return ``(phase, error)`` for ``U_original == exp(i.phase) * U_optimized``.

    This is the same sign convention as :attr:`Module.global_phase`, so the two
    can be cross-checked against each other.
    """
    overlap = np.vdot(ub.reshape(-1), ua.reshape(-1))
    if abs(overlap) < 1e-12:
        return 0.0, float(np.abs(ua - ub).max())
    phase = float(np.angle(overlap))
    error = float(np.abs(ua - np.exp(1j * phase) * ub).max())
    return phase, error


def _wrap(angle: float) -> float:
    value = math.fmod(angle, 2 * math.pi)
    if value <= -math.pi:
        value += 2 * math.pi
    elif value > math.pi:
        value -= 2 * math.pi
    return value
