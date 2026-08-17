"""Clifford+T optimization: collapse same-axis runs into the cheapest form.

For each qubit this pass collects a maximal run of single-qubit gates about one
Pauli axis — hopping over any gate transparent to that axis, so a CX between
two T gates on its control leg does not break the run — adds the run up in the
``exp(i*phase) R_axis(angle)`` normal form, and re-emits the cheapest sequence
that reproduces it:

===============  ==================  =======
total Z rotation  emitted             T-count
===============  ==================  =======
0                 (nothing)           0
pi/4              T                   1
pi/2              S                   0
3pi/4             S · T               1
pi                Z                   0
===============  ==================  =======

The win is twofold: ``T·T -> S`` removes T gates outright, and an arbitrary
``Rz`` whose angle happens to be a multiple of pi/4 becomes exact Clifford+T
instead of going through rotation synthesis (tens of T gates per rotation on a
fault-tolerant backend).
"""

from __future__ import annotations

from ..ir.dag import commutes_with_axis
from ..ir.module import BasicBlock, InstKind, Module, QuantumOp
from .algebra import axis_form, fold, synthesize_axis
from .rewrite import Pass, PassContext, PassStats, emit_sequence

__all__ = ["CliffordTPass"]


class CliffordTPass(Pass):
    name = "clifford-t"
    description = "fold same-axis runs into minimal Clifford+T sequences"

    def run(self, module: Module, ctx: PassContext) -> PassStats:
        stats = PassStats(self.name)
        for _fn, block in module.blocks():
            self._sweep_block(module, block, ctx, stats)
        return stats

    # -- one left-to-right sweep -----------------------------------------
    def _sweep_block(
        self, module: Module, block: BasicBlock, ctx: PassContext, stats: PassStats
    ) -> None:
        """Fold every same-axis run in one forward pass.

        ``_gather`` already returns the *maximal* run reachable from its
        starting index, so folding it can only ever change instructions at
        or after that index — nothing earlier can become newly foldable.
        That makes a single left-to-right sweep, resuming right where each
        fold left off, a complete fixed point for this pass alone: no need
        to rescan from the top after every rewrite (the old approach, which
        made this pass quadratic in the number of rewrites for long blocks).
        Interactions with *other* passes are still handled by the pipeline's
        own outer fixed-point loop in :class:`~qizil.passes.manager.PassManager`.
        """
        i = 0
        guard = 0
        limit = 4 * len(block.instructions) + 16
        while i < len(block.instructions):
            guard += 1
            if guard > limit:  # pragma: no cover - defensive only
                stats.notes.append("fold iteration guard tripped")
                break
            if guard % 32 == 0 and ctx.out_of_time():
                stats.notes.append(
                    f"{self.name}: stopped mid-block -- time budget exceeded"
                )
                break

            inst = block.instructions[i]
            if inst.kind is not InstKind.QUANTUM or inst.op is None:
                i += 1
                continue
            form = axis_form(inst.op)
            if form is None:
                i += 1
                continue
            qubit = inst.op.qubits[0]
            if not qubit.is_definite():
                i += 1
                continue

            members = self._gather(block, i, qubit, form.axis)
            forms = [axis_form(block.instructions[m].op) for m in members]
            angle, phase = fold(forms)
            emission = synthesize_axis(form.axis, angle, phase, ctx.policy)
            if emission is None:
                i += 1
                continue
            original = [_signature(block.instructions[m].op) for m in members]
            if _same(original, list(emission.gates), ctx.tol):
                i += 1
                continue

            self._apply(module, block, members, emission, qubit, stats)
            i = members[0]  # re-examine from here; nothing before it changed

    def _gather(
        self, block: BasicBlock, start: int, qubit, axis: str
    ) -> list[int]:
        """Indices of the same-axis run reachable from ``start``."""
        members = [start]
        for j in range(start + 1, len(block.instructions)):
            other = block.instructions[j]
            if other.kind in (InstKind.CLASSICAL, InstKind.TRIVIA):
                continue
            if other.kind in (InstKind.BARRIER, InstKind.TERMINATOR):
                break
            if other.kind is not InstKind.QUANTUM or other.op is None:
                break
            op = other.op
            other_form = axis_form(op)
            if (
                other_form is not None
                and other_form.axis == axis
                and op.qubits[0].same_as(qubit)
            ):
                members.append(j)
                continue
            if commutes_with_axis(op, qubit, axis):
                continue
            break
        return members

    def _apply(
        self,
        module: Module,
        block: BasicBlock,
        members: list[int],
        emission,
        qubit,
        stats: PassStats,
    ) -> None:
        last = members[-1]
        new_insts = emit_sequence(
            module,
            emission.gates,
            (qubit,),
            block.instructions[last].indent,
            origin=self.name,
        )
        block.instructions[last : last + 1] = new_insts
        for index in reversed(members[:-1]):
            del block.instructions[index]
        module.global_phase += emission.residual_phase
        stats.rewrites += 1
        stats.removed += len(members)
        stats.inserted += len(new_insts)


def _signature(op: QuantumOp) -> tuple[str, str, float | None]:
    angle = op.angles[0] if op.spec.num_params else None
    return (op.base, op.functor, angle)


def _same(
    original: list[tuple[str, str, float | None]],
    emitted: list[tuple[str, str, float | None]],
    tol: float,
) -> bool:
    if len(original) != len(emitted):
        return False
    for (ba, fa, aa), (bb, fb, ab) in zip(original, emitted):
        if ba != bb or fa != fb:
            return False
        if (aa is None) != (ab is None):
            return False
        if aa is not None and abs(aa - ab) > tol:
            return False
    return True
