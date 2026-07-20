"""Gate identity & cancellation: remove pairs whose product is the identity.

Covers the self-inverse pairs (H·H, X·X, CX·CX, ...), functor pairs (T·T†) and
rotation pairs that sum to zero.  Only pairs that are already adjacent on the
shared qubits are considered here; :mod:`qizil.passes.commutation` extends the
same rewrites through commuting gates.
"""

from __future__ import annotations

from ..ir.module import QuantumOp
from .algebra import axis_form, fold, is_identity, pair_form
from .rewrite import PairPass, PassContext, Rewrite, same_qubits

__all__ = ["CancellationPass", "try_annihilate"]

#: Gates that denote the same unitary under different spellings.
_CANONICAL = {"cnot": "cx", "id": "i"}


def _canon(base: str) -> str:
    return _CANONICAL.get(base, base)


def try_annihilate(a: QuantumOp, b: QuantumOp, ctx: PassContext) -> Rewrite | None:
    """Return a rewrite deleting both gates when ``b @ a == identity``."""
    if not same_qubits(a, b):
        return None

    fa, fb = axis_form(a), axis_form(b)
    if fa is not None and fb is not None and fa.axis == fb.axis:
        angle, phase = fold([fa, fb])
        identity, residual = is_identity(angle, phase, ctx.tol)
        if identity and _phase_ok(residual, ctx):
            return Rewrite((), residual, f"{a.describe()} · {b.describe()} = I")
        return None

    if (
        a.spec.hermitian
        and b.spec.hermitian
        and _canon(a.base) == _canon(b.base)
        and a.spec.num_qubits == b.spec.num_qubits
    ):
        # Hermitian gates are their own inverse, so the functor is irrelevant.
        return Rewrite((), 0.0, f"{a.describe()} · {b.describe()} = I")

    pa, pb = pair_form(a), pair_form(b)
    if pa is not None and pb is not None and pa.axis == pb.axis and a.base == b.base:
        angle, phase = fold([pa, pb])
        identity, residual = is_identity(angle, phase, ctx.tol)
        if identity and _phase_ok(residual, ctx):
            return Rewrite((), residual, f"{a.describe()} · {b.describe()} = I")
    return None


def _phase_ok(residual: float, ctx: PassContext) -> bool:
    return not (
        ctx.policy.preserve_global_phase and abs(residual) > ctx.tol
    )


class CancellationPass(PairPass):
    name = "cancel"
    description = "eliminate adjacent self-inverse and adjoint gate pairs"
    through_commuting = False

    def combine(self, a: QuantumOp, b: QuantumOp, ctx: PassContext) -> Rewrite | None:
        return try_annihilate(a, b, ctx)
