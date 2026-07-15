"""Continuous rotation fusion: Rz(a)·Rz(b) -> Rz(a+b).

Applies to the single-qubit rotations (rx / ry / rz) and to the two-qubit
Pauli rotations (rxx / ryy / rzz) when both act on the same qubits along the
same axis.  Angles must be compile-time constants; a rotation whose angle is an
SSA value is left alone (fusing it would mean emitting classical arithmetic,
and dynamic angles usually come from measurement feedback we must not touch).
"""

from __future__ import annotations

from ..ir.module import QuantumOp
from .algebra import (
    ROT_NAME,
    axis_form,
    fold,
    is_identity,
    normalize_angle,
    pair_form,
    synthesize_axis,
)
from .rewrite import PairPass, PassContext, Rewrite, same_qubits

__all__ = ["RotationMergePass", "try_merge_rotation"]


def try_merge_rotation(
    a: QuantumOp, b: QuantumOp, ctx: PassContext
) -> Rewrite | None:
    """Fuse two same-axis rotations acting on the same qubits."""
    if not same_qubits(a, b):
        return None

    if a.spec.rot_axis is not None and b.spec.rot_axis is not None:
        if a.spec.rot_axis != b.spec.rot_axis:
            return None
        fa, fb = axis_form(a), axis_form(b)
        if fa is None or fb is None:
            return None  # symbolic angle
        angle, phase = fold([fa, fb])
        emission = synthesize_axis(
            fa.axis, angle, phase, ctx.policy, allow_fixed=False
        )
        if emission is None or len(emission.gates) > 1:
            return None
        note = f"{a.describe()} · {b.describe()} -> " + (
            "I" if not emission.gates else f"{ROT_NAME[fa.axis]}({angle:.6g})"
        )
        return Rewrite(emission.gates, emission.residual_phase, note)

    pa, pb = pair_form(a), pair_form(b)
    if pa is not None and pb is not None and a.base == b.base:
        angle, phase = fold([pa, pb])
        identity, residual = is_identity(angle, phase, ctx.tol)
        if identity:
            if ctx.policy.preserve_global_phase and abs(residual) > ctx.tol:
                return None
            return Rewrite((), residual, f"{a.describe()} · {b.describe()} = I")
        out = angle if ctx.policy.preserve_global_phase else normalize_angle(angle)
        residual = normalize_angle(phase - (angle - out) / 2.0)
        if not ctx.policy.can_emit(a.base):
            return None
        return Rewrite(
            ((a.base, "body", out),),
            residual,
            f"{a.describe()} · {b.describe()} -> {a.base}({out:.6g})",
        )
    return None


class RotationMergePass(PairPass):
    name = "merge-rotations"
    description = "fuse adjacent rotations about the same axis"
    through_commuting = False

    def combine(self, a: QuantumOp, b: QuantumOp, ctx: PassContext) -> Rewrite | None:
        return try_merge_rotation(a, b, ctx)
