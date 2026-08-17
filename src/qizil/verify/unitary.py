"""Exact equivalence checking: U_out == U_in (up to a reported global phase).

The check is structural *and* numeric.  Both modules are split into segments at
every instruction that is not a unitary gate — measurements, resets, runtime
calls, classical code, terminators.  Those segments must line up textually, and
each run of gates between them must produce the same unitary matrix.  That way
a rewrite can never hide a change in measurement order or control flow behind a
matching matrix.

Runs with no dependencies (see :mod:`qizil.verify.simulator`); numpy is used
automatically when present, purely for speed and a higher qubit ceiling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..ir.module import InstKind, Instruction, Module, QuantumOp
from . import accelerated, simulator
from .matrices import Unsupported, gate_matrix  # noqa: F401  (re-exported)

__all__ = [
    "VerifyResult",
    "verify_equivalence",
    "unitary_of_segment",
    "gate_matrix",
    "Unsupported",
    "select_backend",
    "max_qubits",
]


def select_backend():
    """The fastest available reference simulator."""
    return accelerated if accelerated.available() else simulator


def max_qubits() -> int:
    return select_backend().MAX_QUBITS


@dataclass
class VerifyResult:
    ok: bool
    checked_segments: int = 0
    skipped_segments: int = 0
    max_error: float = 0.0
    global_phase: float = 0.0
    messages: list[str] = field(default_factory=list)
    #: False when the check could not run at all.  Distinct from ``ok=False``,
    #: which means the modules really are not equivalent.
    available: bool = True
    #: Which reference simulator ran ("python" or "numpy").
    backend: str = ""

    def to_dict(self) -> dict:
        return {
            "equivalent": self.ok,
            "available": self.available,
            "backend": self.backend,
            "segments_checked": self.checked_segments,
            "segments_skipped": self.skipped_segments,
            "max_error": self.max_error,
            "global_phase": self.global_phase,
            "messages": self.messages,
        }


def unitary_of_segment(ops: list[QuantumOp], index: dict, num_qubits: int):
    """Unitary of a run of gates, using the best available backend."""
    return select_backend().unitary_of_segment(ops, index, num_qubits)


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
    backend = select_backend()
    result = VerifyResult(ok=True, backend=backend.name)
    try:
        index = _qubit_index([original, optimized])
    except Unsupported as exc:
        return VerifyResult(
            ok=False, available=False, backend=backend.name, messages=[f"unsupported: {exc}"]
        )
    num_qubits = max(1, len(index))
    if num_qubits > backend.MAX_QUBITS:
        return VerifyResult(
            ok=False,
            available=False,
            backend=backend.name,
            skipped_segments=1,
            messages=[
                f"{num_qubits} qubits exceeds the {backend.MAX_QUBITS}-qubit limit"
                + ("" if backend is accelerated else " (install numpy for up to 12)")
            ],
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
                    ua = backend.unitary_of_segment(ops_a, index, num_qubits)
                    ub = backend.unitary_of_segment(ops_b, index, num_qubits)
                except Unsupported as exc:
                    result.skipped_segments += 1
                    result.messages.append(f"skipped segment: {exc}")
                    continue
                phase, error = backend.compare(ua, ub)
                result.checked_segments += 1
                result.max_error = max(result.max_error, error)
                total_phase += phase
                if error > tol:
                    result.ok = False
                    result.messages.append(
                        f"@{name}/{block_a.label}: unitary differs (error {error:.3e})"
                    )
    if result.ok and result.checked_segments == 0 and result.skipped_segments > 0:
        # Every segment that existed was skipped (too many qubits, or too
        # large to check in reasonable time) -- there is zero actual
        # evidence for equivalence here, so this must not report `ok=True`.
        # (A module with no quantum content at all also has
        # checked_segments == 0, but skipped_segments == 0 too in that
        # case, and vacuous equivalence there is correct to report.)
        result.ok = False
        result.available = False
        result.messages.append(
            "no segment could be checked -- every segment was skipped "
            "(see messages above), so equivalence is unverified, not confirmed"
        )
    result.global_phase = _wrap(total_phase)
    return result


def _wrap(angle: float) -> float:
    value = math.fmod(angle, 2 * math.pi)
    if value <= -math.pi:
        value += 2 * math.pi
    elif value > math.pi:
        value -= 2 * math.pi
    return value
