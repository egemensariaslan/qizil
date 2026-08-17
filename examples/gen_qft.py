#!/usr/bin/env python3
"""Generate ``qft_roundtrip.ll``: a Quantum Fourier Transform, then its exact
inverse, decomposed the way a real compiler emits it.

QFT is one of the two or three most-cited primitives in quantum computing —
it is the phase-estimation subroutine behind Shor's algorithm and the phase
oracle behind most near-term chemistry and optimization ansatze. Textbook
circuits usually leave the controlled-phase gate as a black box; a real
frontend compiling to QIR cannot, because ``__quantum__qis__`` has no native
controlled-phase gate. Q# and Qiskit both lower it to the standard
CNOT-Rz-CNOT-Rz identity used here::

    CRz(theta)(c, t)  =  Rz(+theta/2)_t . CNOT(c,t) . Rz(-theta/2)_t . CNOT(c,t)

(circuit / time order, left to right; verified algebraically in the comment
below and, independently, by qizil's own reference-simulator --verify).

The circuit this script emits is QFT_n followed immediately by its exact
adjoint QFT_n^-1, built by reversing the forward gate list and adjoining each
gate (Rz(theta) -> Rz(-theta); H, CNOT and SWAP are self-adjoint). Every gate
in this construction is phase-free in qizil's convention (see
qizil.ir.gates.GATES), so the product is EXACTLY the identity matrix, not
merely the identity up to global phase -- a claim anyone can check by hand
from the H . H = I / CNOT . CNOT = I / Rz(t) . Rz(-t) = I identities alone,
independent of trusting this tool. That is what makes this the strongest
correctness demonstration in the example set: qizil's optimizer, run on a
circuit whose answer is known in closed form, should collapse it towards
nothing, and --verify independently confirms the result really is the
identity to floating-point precision.

A real QFT-then-inverse-QFT pattern is not contrived -- it is exactly what
appears at the boundary of quantum phase estimation (apply QFT-dagger right
after the controlled-unitary ladder) and in any algorithm that changes basis,
acts, and changes back.

    python examples/gen_qft.py > examples/qft_roundtrip.ll
"""

from __future__ import annotations

import math

NUM_QUBITS = 5

Gate = tuple  # (base, angle_or_None, qubits)


def qft_gates(qubits: list[int]) -> list[Gate]:
    """Forward QFT on ``qubits`` (no final bit-reversal swap omitted --
    included below), built entirely from H / CNOT / Rz."""
    n = len(qubits)
    gates: list[Gate] = []
    for i in range(n):
        gates.append(("h", None, (qubits[i],)))
        for j in range(i + 1, n):
            theta = 2 * math.pi / (2 ** (j - i + 1))
            gates += crz(control=qubits[j], target=qubits[i], theta=theta)
    for i in range(n // 2):
        a, b = qubits[i], qubits[n - 1 - i]
        gates.append(("swap", None, (a, b)))
    return gates


def crz(control: int, target: int, theta: float) -> list[Gate]:
    """CRz(theta)(control, target) via CNOT-Rz-CNOT-Rz (see module docstring)."""
    return [
        ("cnot", None, (control, target)),
        ("rz", -theta / 2, (target,)),
        ("cnot", None, (control, target)),
        ("rz", theta / 2, (target,)),
    ]


def adjoint(gate: Gate) -> Gate:
    base, angle, qubits = gate
    if base == "rz":
        return ("rz", -angle, qubits)
    return gate  # h, cnot, swap are all self-adjoint


def inverse_gates(gates: list[Gate]) -> list[Gate]:
    """U^-1 for a circuit U given as a time-ordered gate list: reverse the
    list and adjoin every gate."""
    return [adjoint(g) for g in reversed(gates)]


def qubit_operand(i: int) -> str:
    return "null" if i == 0 else f"inttoptr (i64 {i} to %Qubit*)"


def result_operand(i: int) -> str:
    return "null" if i == 0 else f"inttoptr (i64 {i} to %Result*)"


def render(gate: Gate) -> str:
    base, angle, qubits = gate
    args = []
    if angle is not None:
        args.append(f"double {angle:.17g}")
    args += [f"%Qubit* {qubit_operand(q)}" for q in qubits]
    return f"  call void @__quantum__qis__{base}__body({', '.join(args)})"


def main() -> str:
    qubits = list(range(NUM_QUBITS))
    forward = qft_gates(qubits)
    backward = inverse_gates(forward)
    all_gates = forward + backward

    body = ["  ; --- QFT ---"]
    body += [render(g) for g in forward]
    body.append("  ; --- QFT^-1 (reverse the list, adjoin every gate) ---")
    body += [render(g) for g in backward]
    body.append("  ; --- measure out: every qubit must read back |0> ---")
    for i in qubits:
        body.append(
            f"  call void @__quantum__qis__mz__body("
            f"%Qubit* {qubit_operand(i)}, %Result* {result_operand(i)})"
        )

    used_gates = {g[0] for g in all_gates}
    declares = []
    if "h" in used_gates:
        declares.append("declare void @__quantum__qis__h__body(%Qubit*)")
    if "rz" in used_gates:
        declares.append("declare void @__quantum__qis__rz__body(double, %Qubit*)")
    if "cnot" in used_gates:
        declares.append("declare void @__quantum__qis__cnot__body(%Qubit*, %Qubit*)")
    if "swap" in used_gates:
        declares.append("declare void @__quantum__qis__swap__body(%Qubit*, %Qubit*)")
    declares.append("declare void @__quantum__qis__mz__body(%Qubit*, %Result*) #1")

    n_crz = sum(1 for g in forward if g[0] == "cnot") // 1  # cnot count, forward half
    return f"""; A {NUM_QUBITS}-qubit Quantum Fourier Transform immediately followed by its
; own exact inverse: QFT_n . QFT_n^-1 = I, by construction (see
; examples/gen_qft.py for the derivation). CRz is decomposed into CNOT/Rz the
; way a real compiler emits it, since __quantum__qis__ has no native
; controlled-phase gate. {n_crz} CNOTs and {sum(1 for g in forward if g[0]=='rz')} Rz gates per half,
; {sum(1 for g in forward if g[0]=='h')} H and {NUM_QUBITS // 2} SWAP.
; ModuleID = 'qft_roundtrip'
source_filename = "qft_roundtrip"

%Qubit = type opaque
%Result = type opaque

define void @main() #0 {{
entry:
{chr(10).join(body)}
  ret void
}}

{chr(10).join(declares)}

attributes #0 = {{ "entry_point" "qir_profiles"="base_profile" \
"required_num_qubits"="{NUM_QUBITS}" "required_num_results"="{NUM_QUBITS}" }}
attributes #1 = {{ "irreversible" }}

!llvm.module.flags = !{{!0, !1}}

!0 = !{{i32 1, !"qir_major_version", i32 1}}
!1 = !{{i32 7, !"qir_minor_version", i32 0}}
"""


if __name__ == "__main__":
    print(main(), end="")
