"""A drawable description of a module: qubit wires, time columns, gate shapes.

This is the bridge between the instruction model and any renderer.  It answers
the three questions a circuit diagram needs — which wire, which column, which
shape — and nothing else, so the same payload drives the interactive UI and the
static HTML report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..ir.module import InstKind, Module

__all__ = ["describe", "pi_label", "CircuitBlock"]

PI = math.pi
_TOL = 1e-9

#: How a gate is drawn.  Anything not listed falls back to a labelled box.
SHAPES: dict[str, str] = {
    "cnot": "control-target",
    "cx": "control-target",
    "ccx": "control-control-target",
    "cz": "control-control",
    "cy": "control-box",
    "swap": "swap",
    "rzz": "pair",
    "rxx": "pair",
    "ryy": "pair",
}

#: Display names; anything else is upper-cased.
LABELS: dict[str, str] = {
    "cnot": "X",
    "cx": "X",
    "ccx": "X",
    "cy": "Y",
    "rx": "Rx",
    "ry": "Ry",
    "rz": "Rz",
    "rxx": "XX",
    "ryy": "YY",
    "rzz": "ZZ",
    "mz": "M",
    "m": "M",
    "measure": "M",
    "reset": "|0>",
    "read_result": "?",
}


def pi_label(angle: float | None) -> str | None:
    """Render an angle as a multiple of pi when it is one: ``3pi/4``, ``-pi/2``."""
    if angle is None:
        return None
    if abs(angle) < _TOL:
        return "0"
    ratio = angle / PI
    for denominator in (1, 2, 3, 4, 6, 8, 12, 16):
        numerator = ratio * denominator
        nearest = round(numerator)
        if nearest and abs(numerator - nearest) < 1e-9:
            sign = "-" if nearest < 0 else ""
            magnitude = abs(nearest)
            head = "pi" if magnitude == 1 else f"{magnitude}pi"
            return f"{sign}{head}" if denominator == 1 else f"{sign}{head}/{denominator}"
    return f"{angle:.4g}"


@dataclass
class CircuitBlock:
    function: str
    label: str | None
    ops: list[dict] = field(default_factory=list)
    #: Width of the greedy visual layout, in gate-columns.  A rendering
    #: artifact, not the circuit-theoretic depth reported by
    #: ``qizil.analysis.metrics.Metrics.depth`` (the DAG's longest
    #: dependency chain): a long-range 2-qubit gate reserves every column it
    #: visually spans, even on wires it does not touch, so this number can
    #: run higher than the true depth.  Never label it "depth" in the UI.
    columns: int = 0

    def to_dict(self) -> dict:
        return {
            "function": self.function,
            "label": self.label,
            "columns": self.columns,
            "ops": self.ops,
        }


def _qubit_order(module: Module) -> dict:
    """Map every qubit reference to a wire index, static ids first and in order."""
    static: set = set()
    dynamic: list = []
    for _fn, block in module.blocks():
        for inst in block.instructions:
            if inst.kind is not InstKind.QUANTUM or inst.op is None:
                continue
            for q in inst.op.qubits:
                if q.kind.value == "static":
                    static.add((q.id, q.key))
                elif q.key not in dynamic:
                    dynamic.append(q.key)
    order = [key for _id, key in sorted(static)] + dynamic
    return {key: i for i, key in enumerate(order)}


def _wire_labels(index: dict) -> list[str]:
    labels = [""] * len(index)
    for key, position in index.items():
        kind, name = key[1], key[2]
        labels[position] = f"q{name}" if kind == "static" else str(name)
    return labels


def describe(module: Module, max_ops: int = 4000) -> dict:
    """Describe ``module`` as wires, columns and gate shapes."""
    index = _qubit_order(module)
    wires = _wire_labels(index)
    blocks: list[CircuitBlock] = []
    truncated = False
    budget = max_ops

    for fn, block in module.blocks():
        entry = CircuitBlock(function=fn.name, label=block.label)
        next_column = [0] * max(1, len(index))
        for inst in block.instructions:
            if inst.kind is InstKind.TRIVIA:
                continue
            if budget <= 0:
                truncated = True
                break

            if inst.kind is InstKind.BARRIER:
                column = max(next_column, default=0)
                entry.ops.append(
                    {
                        "kind": "barrier",
                        "column": column,
                        "qubits": [],
                        "label": _barrier_label(inst.text),
                        "text": inst.text.strip(),
                    }
                )
                next_column = [column + 1] * len(next_column)
                budget -= 1
                continue

            if inst.kind is not InstKind.QUANTUM or inst.op is None:
                continue

            op = inst.op
            lanes = [index[q.key] for q in op.qubits if q.key in index]
            if not lanes:
                continue
            span = range(min(lanes), max(lanes) + 1)
            column = max((next_column[w] for w in span), default=0)
            angle = op.angles[0] if op.spec.num_params and op.angles else None
            entry.ops.append(
                {
                    "kind": op.spec.kind,
                    "column": column,
                    "qubits": lanes,
                    "name": op.base,
                    "label": LABELS.get(op.base, op.base.upper()),
                    "shape": SHAPES.get(op.base, "box"),
                    "adjoint": op.functor == "adj",
                    "angle": angle,
                    "angle_label": pi_label(angle),
                    "results": [r.id for r in op.results if r.id is not None],
                    "text": inst.text.strip(),
                }
            )
            for w in span:
                next_column[w] = column + 1
            budget -= 1
        entry.columns = max(next_column, default=0)
        blocks.append(entry)

    return {
        "wires": wires,
        "blocks": [b.to_dict() for b in blocks],
        "truncated": truncated,
    }


def _barrier_label(text: str) -> str:
    stripped = text.strip()
    if "__quantum__rt__" in stripped:
        name = stripped.split("__quantum__rt__", 1)[1].split("(")[0]
        return f"rt.{name}"
    if "@" in stripped:
        return stripped.split("@", 1)[1].split("(")[0].replace("__quantum__qis__", "")
    return stripped.split()[0] if stripped else "?"
