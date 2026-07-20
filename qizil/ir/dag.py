"""Quantum Instruction Graph: per-basic-block dependency DAG + commutation.

Two questions drive every pass, and both are answered here:

* **What depends on what?** :class:`BlockDag` links each quantum instruction to
  the previous instruction that may touch the same qubit or result.  Anything
  Qizil cannot model (runtime calls, controlled functors, opaque qubit
  pointers) becomes a *universal* node that everything is ordered against, so
  classical control flow and measurement feedback can never be reordered.

* **May these two swap?** :func:`commutes` answers with the Pauli-axis rule:
  if, on every qubit the two gates share, both act through the same Pauli axis,
  their generators commute and so do they.  ``H`` declares no axis and so
  commutes with nothing it shares a qubit with.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .module import BasicBlock, InstKind, Instruction, QuantumOp

__all__ = [
    "DagNode",
    "BlockDag",
    "commutes",
    "commutes_with_axis",
    "can_move_forward",
    "can_move_backward",
]


@dataclass
class DagNode:
    id: int
    index: int  # position within block.instructions
    inst: Instruction
    universal: bool  # orders against every other node
    preds: set[int] = field(default_factory=set)
    succs: set[int] = field(default_factory=set)

    @property
    def op(self) -> QuantumOp | None:
        return self.inst.op

    @property
    def label(self) -> str:
        if self.inst.op is not None:
            return self.inst.op.describe()
        return self.inst.text.strip()[:40]


class BlockDag:
    """Dependency graph over the quantum-relevant instructions of one block."""

    def __init__(self, block: BasicBlock):
        self.block = block
        self.nodes: list[DagNode] = []
        self._build()

    # -- construction ---------------------------------------------------
    def _build(self) -> None:
        last_touch: dict[tuple, int] = {}
        last_universal: int | None = None

        for index, inst in enumerate(self.block.instructions):
            if inst.kind is InstKind.QUANTUM:
                refs = list(inst.op.qubits) + list(inst.op.results)
                universal = any(not r.is_definite() for r in refs)
            elif inst.kind is InstKind.BARRIER:
                refs = []
                universal = True
            else:
                continue

            node = DagNode(id=len(self.nodes), index=index, inst=inst, universal=universal)
            self.nodes.append(node)

            if universal:
                preds = set(last_touch.values())
                if last_universal is not None:
                    preds.add(last_universal)
                self._link(preds, node)
                last_touch.clear()
                last_universal = node.id
            else:
                preds = set()
                for ref in refs:
                    prev = last_touch.get(ref.key, last_universal)
                    if prev is not None:
                        preds.add(prev)
                self._link(preds, node)
                for ref in refs:
                    last_touch[ref.key] = node.id

    def _link(self, preds: Iterable[int], node: DagNode) -> None:
        for p in preds:
            if p == node.id:
                continue
            node.preds.add(p)
            self.nodes[p].succs.add(node.id)

    # -- queries --------------------------------------------------------
    def gate_nodes(self) -> list[DagNode]:
        return [
            n
            for n in self.nodes
            if not n.universal and n.op is not None and n.op.is_gate
        ]

    def node_at(self, index: int) -> DagNode | None:
        for n in self.nodes:
            if n.index == index:
                return n
        return None

    def depth(self) -> int:
        """Longest chain of dependent quantum instructions in this block."""
        best = 0
        level: dict[int, int] = {}
        for node in self.nodes:  # nodes are in program order
            level[node.id] = 1 + max((level[p] for p in node.preds), default=0)
            best = max(best, level[node.id])
        return best

    def to_dot(self, name: str = "block") -> str:
        lines = [f'digraph "{name}" {{', "  rankdir=TB;", '  node [shape=box, fontname="monospace"];']
        for node in self.nodes:
            shape = "octagon" if node.universal else "box"
            label = node.label.replace('"', '\\"')
            lines.append(f'  n{node.id} [label="{label}", shape={shape}];')
        for node in self.nodes:
            for succ in sorted(node.succs):
                lines.append(f"  n{node.id} -> n{succ};")
        lines.append("}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Commutation
# --------------------------------------------------------------------------


def _shares_any(a: QuantumOp, b: QuantumOp) -> bool:
    for ra in list(a.qubits) + list(a.results):
        for rb in list(b.qubits) + list(b.results):
            if ra.may_alias(rb):
                return True
    return False


def _identical(a: QuantumOp, b: QuantumOp) -> bool:
    return (
        a.func == b.func
        and a.angle_texts == b.angle_texts
        and all(q.is_definite() for q in a.qubits)
        and a.qubit_keys() == b.qubit_keys()
    )


def commutes(a: QuantumOp, b: QuantumOp) -> bool:
    """Whether two quantum operations can be swapped without changing the unitary."""
    if not _shares_any(a, b):
        return True
    if a.spec.kind != "gate" or b.spec.kind != "gate":
        # Measurement / reset / readout: only order-independent when disjoint.
        return False
    if _identical(a, b):
        return True
    for i, qa in enumerate(a.qubits):
        for j, qb in enumerate(b.qubits):
            if not qa.may_alias(qb):
                continue
            if not (a.spec.slot_axes(i) & b.spec.slot_axes(j)):
                return False
    return True


def commutes_with_axis(op: QuantumOp, qubit, axis: str) -> bool:
    """Does ``op`` commute with a rotation about ``axis`` on ``qubit``?

    Used when collecting a run of same-axis gates: the run may hop over any
    gate that is transparent to that axis on that qubit (a CX control leg is
    transparent to Z, a CX target leg to X, and so on).
    """
    touches = any(q.may_alias(qubit) for q in op.qubits) or any(
        r.may_alias(qubit) for r in op.results
    )
    if not touches:
        return True
    if op.spec.kind != "gate":
        return False
    for i, q in enumerate(op.qubits):
        if q.may_alias(qubit) and axis not in op.spec.slot_axes(i):
            return False
    return True


# --------------------------------------------------------------------------
# Legality of moving an instruction inside its block
# --------------------------------------------------------------------------


def _blocking(inst: Instruction, op: QuantumOp) -> bool:
    """True if ``op`` cannot be swapped past ``inst``."""
    if inst.kind is InstKind.QUANTUM:
        return not commutes(op, inst.op)
    # Classical instructions and trivia never touch quantum state.
    return inst.kind in (InstKind.BARRIER, InstKind.TERMINATOR)


def can_move_forward(block: BasicBlock, src: int, dst: int) -> bool:
    """Can the instruction at ``src`` be moved to just before ``dst``?

    Moving later never breaks SSA dominance: a gate defines no value that a
    following instruction could consume.
    """
    inst = block.instructions[src]
    if inst.op is None:
        return False
    return all(
        not _blocking(block.instructions[k], inst.op) for k in range(src + 1, dst)
    )


def can_move_backward(block: BasicBlock, src: int, dst: int) -> bool:
    """Can the instruction at ``src`` be moved back to position ``dst``?

    Besides commutation this requires every SSA operand to still be defined
    before the new position.
    """
    inst = block.instructions[src]
    if inst.op is None:
        return False
    if any(_blocking(block.instructions[k], inst.op) for k in range(dst, src)):
        return False
    for k in range(dst, src):
        other = block.instructions[k]
        if other.assign is not None and other.assign in inst.uses:
            return False
    return True
