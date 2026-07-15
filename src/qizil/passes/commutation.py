"""Commutation-based reordering.

Runs the cancellation and rotation-fusion rewrites again, but this time the
search for a partner walks *through* gates that commute with the candidate.
Sliding those gates out of the way is a no-op on the unitary, so a pair that is
separated only by commuting gates is really an adjacent pair::

    T(q0) · CX(q0,q1) · T(q0)   ->   CX(q0,q1) · S(q0)

because the CX control leg is diagonal and therefore commutes with T.  The
commutation rule itself lives in :func:`qizil.ir.dag.commutes`.
"""

from __future__ import annotations

from ..ir.module import QuantumOp
from .cancellation import try_annihilate
from .rewrite import PairPass, PassContext, Rewrite
from .rotation import try_merge_rotation

__all__ = ["CommutationPass"]


class CommutationPass(PairPass):
    name = "commute"
    description = "cancel and fuse gates separated only by commuting gates"
    through_commuting = True

    def combine(self, a: QuantumOp, b: QuantumOp, ctx: PassContext) -> Rewrite | None:
        rewrite = try_annihilate(a, b, ctx)
        if rewrite is not None:
            return rewrite
        return try_merge_rotation(a, b, ctx)
