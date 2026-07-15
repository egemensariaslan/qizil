"""Circuit metrics: gate counts, depth, and the fault-tolerance-relevant split.

The headline number for fault-tolerant compilation is not "how many gates" but
"how many T gates", and an arbitrary-angle rotation is far more expensive than
a T because it has to be synthesized from Clifford+T.  Metrics therefore
separate three classes of non-Clifford work:

* ``t_gates``          - literal T / T-dagger
* ``quarter_rotations`` - rotations by an odd multiple of pi/4 (exactly 1 T each)
* ``arbitrary_rotations`` - everything else (needs rotation synthesis)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..ir.dag import BlockDag
from ..ir.module import InstKind, Module

__all__ = ["Metrics", "measure", "CLIFFORD_GATES"]

PI = math.pi
TOL = 1e-9

CLIFFORD_GATES = frozenset(
    {"i", "id", "x", "y", "z", "h", "s", "cnot", "cx", "cy", "cz", "swap"}
)

_NUM_QUBITS_ATTR = re.compile(r'"required_num_qubits"="(\d+)"')


@dataclass
class Metrics:
    qubits: int = 0
    total_instructions: int = 0
    gates: int = 0
    one_qubit_gates: int = 0
    two_qubit_gates: int = 0
    multi_qubit_gates: int = 0
    t_gates: int = 0
    quarter_rotations: int = 0
    arbitrary_rotations: int = 0
    clifford_gates: int = 0
    measurements: int = 0
    resets: int = 0
    opaque_instructions: int = 0
    #: Sum of the per-block dependency depths.  For straight-line QIR (one
    #: block) this is the circuit depth; for branching code it is the
    #: worst case in which every block executes.
    depth: int = 0
    gate_histogram: dict[str, int] = field(default_factory=dict)

    @property
    def t_count(self) -> int:
        """T gates that are already exact (rotation synthesis excluded)."""
        return self.t_gates + self.quarter_rotations

    def to_dict(self) -> dict:
        return {
            "qubits": self.qubits,
            "quantum_instructions": self.total_instructions,
            "gates": self.gates,
            "one_qubit_gates": self.one_qubit_gates,
            "two_qubit_gates": self.two_qubit_gates,
            "multi_qubit_gates": self.multi_qubit_gates,
            "t_gates": self.t_gates,
            "quarter_rotations": self.quarter_rotations,
            "arbitrary_rotations": self.arbitrary_rotations,
            "exact_t_count": self.t_count,
            "clifford_gates": self.clifford_gates,
            "measurements": self.measurements,
            "resets": self.resets,
            "opaque_instructions": self.opaque_instructions,
            "depth": self.depth,
            "gate_histogram": dict(sorted(self.gate_histogram.items())),
        }


def _classify_rotation(angle: float | None) -> str:
    """"clifford" | "quarter" | "arbitrary" for a rotation angle."""
    if angle is None:
        return "arbitrary"
    k = angle / (PI / 2)
    if abs(k - round(k)) < TOL:
        return "clifford"
    k = angle / (PI / 4)
    if abs(k - round(k)) < TOL:
        return "quarter"
    return "arbitrary"


def measure(module: Module) -> Metrics:
    """Compute metrics for a whole module."""
    m = Metrics()
    histogram: Counter[str] = Counter()
    qubit_keys: set = set()
    dynamic_qubits = False

    for _fn, block in module.blocks():
        for inst in block.instructions:
            if inst.kind is InstKind.BARRIER:
                m.opaque_instructions += 1
                continue
            if inst.kind is not InstKind.QUANTUM or inst.op is None:
                continue
            op = inst.op
            m.total_instructions += 1
            for q in op.qubits:
                if q.is_definite():
                    qubit_keys.add(q.key)
                else:
                    dynamic_qubits = True

            label = op.base if op.functor == "body" else f"{op.base}†"
            histogram[label] += 1

            if op.spec.kind == "measure":
                m.measurements += 1
                continue
            if op.spec.kind == "reset":
                m.resets += 1
                continue
            if op.spec.kind != "gate":
                continue

            m.gates += 1
            if op.spec.num_qubits == 1:
                m.one_qubit_gates += 1
            elif op.spec.num_qubits == 2:
                m.two_qubit_gates += 1
            else:
                m.multi_qubit_gates += 1

            if op.base == "t":
                m.t_gates += 1
            elif op.spec.is_rotation:
                kind = _classify_rotation(op.angles[0] if op.angles else None)
                if kind == "quarter":
                    m.quarter_rotations += 1
                elif kind == "arbitrary":
                    m.arbitrary_rotations += 1
                else:
                    m.clifford_gates += 1
            elif op.base in CLIFFORD_GATES:
                m.clifford_gates += 1

        m.depth += BlockDag(block).depth()

    m.gate_histogram = dict(histogram)
    declared = 0
    for item in module.items:
        if hasattr(item, "lines"):
            for line in item.lines:
                found = _NUM_QUBITS_ATTR.search(line)
                if found:
                    declared = max(declared, int(found.group(1)))
    for fn in module.functions:
        found = _NUM_QUBITS_ATTR.search(fn.header)
        if found:
            declared = max(declared, int(found.group(1)))
    m.qubits = max(len(qubit_keys), declared)
    if dynamic_qubits:
        m.qubits = max(m.qubits, len(qubit_keys) + 1)
    return m
