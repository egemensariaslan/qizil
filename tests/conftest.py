"""Shared test helpers: build QIR text from a compact gate description."""

from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from qizil.ir.gates import GATES, qis_name  # noqa: E402
from qizil.ir.values import format_double  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")

# (base, functor, angle, qubits)
Gate = tuple


def qubit_operand(index: int) -> str:
    return "null" if index == 0 else f"inttoptr (i64 {index} to %Qubit*)"


def result_operand(index: int) -> str:
    return "null" if index == 0 else f"inttoptr (i64 {index} to %Result*)"


def render_call(base: str, functor: str, angle, qubits, result=None) -> str:
    spec = GATES[base]
    args = []
    if spec.num_params:
        args.append(f"double {format_double(angle)}")
    args.extend(f"%Qubit* {qubit_operand(q)}" for q in qubits)
    if result is not None:
        args.append(f"%Result* {result_operand(result)}")
    ret = spec.signature.split("(")[0].strip()
    return f"  call {ret} @{qis_name(base, functor)}({', '.join(args)})"


def make_ir(gates, num_qubits: int = 3, measure: bool = False, name="test") -> str:
    """Assemble a base-profile QIR module from ``(base, functor, angle, qubits)``."""
    body = [render_call(*g) for g in gates]
    used = {(g[0], g[1]) for g in gates}
    if measure:
        for q in range(num_qubits):
            body.append(render_call("mz", "body", None, [q], result=q))
        used.add(("mz", "body"))
    declares = []
    for base, functor in sorted(used):
        spec = GATES[base]
        ret, _, params = spec.signature.partition(" ")
        declares.append(f"declare {ret} @{qis_name(base, functor)}{params.strip()}")
    return f"""; ModuleID = '{name}'
source_filename = "{name}"

%Qubit = type opaque
%Result = type opaque

define void @main() #0 {{
entry:
{chr(10).join(body)}
  ret void
}}

{chr(10).join(declares)}

attributes #0 = {{ "entry_point" "required_num_qubits"="{num_qubits}" "required_num_results"="{num_qubits}" }}
"""


ONE_QUBIT_POOL = ["h", "x", "y", "z", "s", "t", "i"]
ROTATION_POOL = ["rx", "ry", "rz"]
TWO_QUBIT_POOL = ["cnot", "cz", "swap", "cy"]
ANGLE_POOL = [0.0, 0.7853981633974483, 1.5707963267948966, 3.141592653589793, 0.37, -1.2]


def random_gates(rng: random.Random, count: int, num_qubits: int) -> list:
    """A random circuit, biased towards patterns the passes should exploit."""
    gates = []
    for _ in range(count):
        roll = rng.random()
        if roll < 0.45:
            base = rng.choice(ONE_QUBIT_POOL)
            functor = "adj" if base in ("s", "t") and rng.random() < 0.3 else "body"
            gates.append((base, functor, None, [rng.randrange(num_qubits)]))
        elif roll < 0.7:
            base = rng.choice(ROTATION_POOL)
            gates.append(
                (base, "body", rng.choice(ANGLE_POOL), [rng.randrange(num_qubits)])
            )
        elif roll < 0.95:
            base = rng.choice(TWO_QUBIT_POOL)
            a, b = rng.sample(range(num_qubits), 2)
            gates.append((base, "body", None, [a, b]))
        else:
            base = "rzz" if rng.random() < 0.5 else "rxx"
            a, b = rng.sample(range(num_qubits), 2)
            gates.append((base, "body", rng.choice(ANGLE_POOL), [a, b]))
    return gates


@pytest.fixture
def rng():
    return random.Random(20260816)
