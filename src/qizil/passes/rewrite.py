"""Shared machinery for peephole passes: candidate search and instruction edits."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..ir.dag import commutes
from ..ir.gates import GATES, GateSpec, qis_name
from ..ir.module import (
    BasicBlock,
    InstKind,
    Instruction,
    Module,
    QuantumOp,
)
from ..ir.values import Operand, PointerRef, format_double
from .algebra import SynthesisPolicy

__all__ = [
    "Rewrite",
    "PassContext",
    "PassStats",
    "Pass",
    "PairPass",
    "build_gate_inst",
    "same_qubits",
    "forward_candidates",
]


# --------------------------------------------------------------------------
# Pass plumbing
# --------------------------------------------------------------------------


@dataclass
class PassContext:
    policy: SynthesisPolicy
    tol: float = 1e-9
    max_iterations: int = 8
    #: Absolute ``time.monotonic()`` deadline, or ``None`` for unlimited.
    #: Checked between whole passes (PassManager) and periodically inside a
    #: pass's own search loop (PairPass), so a pathological or adversarial
    #: circuit degrades to "stops early with a truncated-but-still-correct
    #: result" instead of hanging.  Every individual rewrite already
    #: preserves the unitary on its own, so stopping mid-pipeline can only
    #: ever leave the module under-optimized -- never wrong.
    deadline: float | None = None

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline


@dataclass
class PassStats:
    name: str
    removed: int = 0
    inserted: int = 0
    rewrites: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.rewrites > 0

    def merge(self, other: PassStats) -> None:
        self.removed += other.removed
        self.inserted += other.inserted
        self.rewrites += other.rewrites
        self.notes.extend(other.notes)

    def to_dict(self) -> dict:
        return {
            "pass": self.name,
            "rewrites": self.rewrites,
            "instructions_removed": self.removed,
            "instructions_inserted": self.inserted,
            "notes": self.notes,
        }


class Pass:
    """Base class: a module-level transformation."""

    name = "pass"
    description = ""

    def run(self, module: Module, ctx: PassContext) -> PassStats:  # pragma: no cover
        raise NotImplementedError


@dataclass
class Rewrite:
    """Replace two matched gates with ``gates`` (possibly empty)."""

    gates: tuple[tuple[str, str, float | None], ...]
    residual_phase: float = 0.0
    note: str = ""


# --------------------------------------------------------------------------
# Instruction construction
# --------------------------------------------------------------------------


def build_gate_inst(
    module: Module,
    base: str,
    functor: str,
    qubits: tuple[PointerRef, ...],
    angle: float | None,
    indent: str = "  ",
    origin: str | None = None,
) -> Instruction:
    """Create a new ``call void @__quantum__qis__<base>__<functor>(...)``."""
    spec: GateSpec = GATES[base]
    func = qis_name(base, functor)
    module.ensure_declaration(func, spec.signature)

    args: list[Operand] = []
    if spec.num_params:
        if angle is None:
            raise ValueError(f"gate {base} requires an angle")
        args.append(Operand("double", format_double(angle)))
    for q in qubits:
        args.append(Operand("%Qubit*", q.text))

    ret = spec.signature.split("(")[0].strip()
    op = QuantumOp(
        func=func,
        base=base,
        functor=functor,
        spec=spec,
        args=args,
        qubits=tuple(qubits),
        angles=(angle,) if spec.num_params else (),
        angle_texts=(format_double(angle),) if spec.num_params and angle is not None else (),
    )
    inst = Instruction(
        text="",
        kind=InstKind.QUANTUM,
        indent=indent,
        opcode="call",
        op=op,
        call_prefix=f"{indent}call {ret} ",
        call_suffix="",
        dirty=True,
        origin=origin,
    )
    inst.text = inst.render()
    return inst


def emit_sequence(
    module: Module,
    gates: tuple[tuple[str, str, float | None], ...],
    qubits: tuple[PointerRef, ...],
    indent: str,
    origin: str,
) -> list[Instruction]:
    return [
        build_gate_inst(module, base, functor, qubits, angle, indent, origin)
        for base, functor, angle in gates
    ]


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------


def same_qubits(a: QuantumOp, b: QuantumOp) -> bool:
    """Both act on exactly the same, statically known, qubits."""
    if len(a.qubits) != len(b.qubits):
        return False
    if not all(q.is_definite() for q in a.qubits):
        return False
    if not all(q.is_definite() for q in b.qubits):
        return False
    ka, kb = a.qubit_keys(), b.qubit_keys()
    if ka == kb:
        return True
    if a.spec.symmetric and b.spec.symmetric:
        return sorted(map(repr, ka)) == sorted(map(repr, kb))
    return False


def _shares(a: QuantumOp, b: QuantumOp) -> bool:
    for ra in list(a.qubits) + list(a.results):
        for rb in list(b.qubits) + list(b.results):
            if ra.may_alias(rb):
                return True
    return False


def forward_candidates(
    block: BasicBlock,
    i: int,
    through_commuting: bool,
    deadline: float | None = None,
) -> Iterator[int]:
    """Yield indices of gates that could be paired with the gate at ``i``.

    In adjacency mode only the immediate DAG successor on a shared qubit is a
    candidate.  With ``through_commuting`` the walk continues past every gate
    that commutes with the one at ``i`` — those can be slid out of the way, so
    the pair really is adjacent in the reordered circuit.  That walk has no
    other stopping condition, so on a circuit with many qubits and little
    overlap between gates it can run the full length of the block (see
    docs/BENCHMARKS.md); passing ``deadline`` (an absolute
    ``time.monotonic()`` timestamp) lets a single call bail out mid-scan
    instead of being the one long step a coarser, only-between-calls check
    would miss.  Stopping mid-scan is always safe: it just means some
    candidates go unchecked, never that a wrong one gets yielded.
    """
    op = block.instructions[i].op
    if op is None:
        return
    for step, j in enumerate(range(i + 1, len(block.instructions))):
        if deadline is not None and step % 32 == 0 and time.monotonic() >= deadline:
            return
        other = block.instructions[j]
        if other.kind in (InstKind.CLASSICAL, InstKind.TRIVIA):
            continue
        if other.kind in (InstKind.BARRIER, InstKind.TERMINATOR):
            return
        if other.kind is not InstKind.QUANTUM or other.op is None:
            return
        if not _shares(op, other.op):
            continue
        yield j
        if not (through_commuting and commutes(op, other.op)):
            return


# --------------------------------------------------------------------------
# Pair-rewriting pass skeleton
# --------------------------------------------------------------------------


class PairPass(Pass):
    """Finds two gates that can be brought together and replaces them."""

    through_commuting = False

    def combine(
        self, a: QuantumOp, b: QuantumOp, ctx: PassContext
    ) -> Rewrite | None:  # pragma: no cover - overridden
        raise NotImplementedError

    #: How many loop iterations between deadline checks.  Deliberately small:
    #: the exact regime this budget exists to bound (many qubits, sparse
    #: overlap -- see docs/BENCHMARKS.md) is one where a *single* iteration's
    #: forward_candidates scan can itself cost O(n), so checking only every
    #: few hundred iterations lets the deadline overshoot by however long
    #: those iterations take, not just by the check's own overhead. A 32-step
    #: interval bounds that overshoot tightly while keeping the
    #: time.monotonic() cost negligible in the fast/common case.
    _DEADLINE_CHECK_EVERY = 32

    def run(self, module: Module, ctx: PassContext) -> PassStats:
        stats = PassStats(self.name)
        for _fn, block in module.blocks():
            if ctx.out_of_time():
                stats.notes.append(
                    f"{self.name}: stopped before this block -- time budget exceeded"
                )
                break
            self._run_block(module, block, ctx, stats)
        return stats

    def _run_block(
        self, module: Module, block: BasicBlock, ctx: PassContext, stats: PassStats
    ) -> None:
        i = 0
        steps = 0
        while i < len(block.instructions):
            steps += 1
            if steps % self._DEADLINE_CHECK_EVERY == 0 and ctx.out_of_time():
                stats.notes.append(
                    f"{self.name}: stopped mid-block -- time budget exceeded "
                    f"(this rewrite pass may be under-applied; the module up to "
                    f"this point is still fully verified-correct)"
                )
                return
            inst = block.instructions[i]
            if inst.kind is not InstKind.QUANTUM or inst.op is None or not inst.op.is_gate:
                i += 1
                continue
            applied = False
            for j in forward_candidates(
                block, i, self.through_commuting, deadline=ctx.deadline
            ):
                other = block.instructions[j]
                if other.op is None or not other.op.is_gate:
                    continue
                rewrite = self.combine(inst.op, other.op, ctx)
                if rewrite is None:
                    continue
                self._apply(module, block, i, j, rewrite, ctx, stats)
                applied = True
                break
            if applied:
                i = max(0, i - 1)
            else:
                i += 1

    def _apply(
        self,
        module: Module,
        block: BasicBlock,
        i: int,
        j: int,
        rewrite: Rewrite,
        ctx: PassContext,
        stats: PassStats,
    ) -> None:
        target = block.instructions[j]
        new_insts = emit_sequence(
            module,
            rewrite.gates,
            target.op.qubits,
            target.indent,
            origin=self.name,
        )
        block.instructions[j : j + 1] = new_insts
        del block.instructions[i]
        module.global_phase += rewrite.residual_phase
        stats.rewrites += 1
        stats.removed += 2
        stats.inserted += len(new_insts)
        if rewrite.note:
            stats.notes.append(rewrite.note)
