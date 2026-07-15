"""Per-pass behaviour on hand-written circuits."""

from __future__ import annotations

import math

import pytest

from conftest import make_ir
from qizil.api import optimize
from qizil.ir.module import InstKind

PI = math.pi


def gate_sequence(module) -> list[tuple[str, str]]:
    return [
        (inst.op.base, inst.op.functor)
        for _fn, block in module.blocks()
        for inst in block.instructions
        if inst.kind is InstKind.QUANTUM
    ]


def angles(module) -> list[float]:
    return [
        inst.op.angles[0]
        for _fn, block in module.blocks()
        for inst in block.instructions
        if inst.kind is InstKind.QUANTUM and inst.op.angles
    ]


def run(gates, num_qubits=2, **kwargs):
    return optimize(make_ir(gates, num_qubits), **kwargs)


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", ["h", "x", "y", "z", "i"])
def test_self_inverse_single_qubit_pairs_cancel(base):
    result = run([(base, "body", None, [0])] * 2, passes=["cancel"])
    assert gate_sequence(result.module) == []


@pytest.mark.parametrize("base", ["cnot", "cz", "swap"])
def test_self_inverse_two_qubit_pairs_cancel(base):
    result = run([(base, "body", None, [0, 1])] * 2, passes=["cancel"])
    assert gate_sequence(result.module) == []


def test_cnot_does_not_cancel_when_operands_are_swapped():
    result = run(
        [("cnot", "body", None, [0, 1]), ("cnot", "body", None, [1, 0])],
        passes=["cancel"],
    )
    assert len(gate_sequence(result.module)) == 2


def test_symmetric_gate_cancels_with_swapped_operands():
    result = run(
        [("cz", "body", None, [0, 1]), ("cz", "body", None, [1, 0])],
        passes=["cancel"],
    )
    assert gate_sequence(result.module) == []


def test_adjoint_pair_cancels():
    result = run(
        [("t", "body", None, [0]), ("t", "adj", None, [0])], passes=["cancel"]
    )
    assert gate_sequence(result.module) == []


def test_opposite_rotations_cancel():
    result = run(
        [("rz", "body", 0.7, [0]), ("rz", "body", -0.7, [0])], passes=["cancel"]
    )
    assert gate_sequence(result.module) == []


def test_odd_run_leaves_one_gate():
    result = run([("h", "body", None, [0])] * 3, passes=["cancel"])
    assert gate_sequence(result.module) == [("h", "body")]


def test_gates_on_different_qubits_do_not_cancel():
    result = run(
        [("h", "body", None, [0]), ("h", "body", None, [1])], passes=["cancel"]
    )
    assert len(gate_sequence(result.module)) == 2


def test_intervening_non_commuting_gate_blocks_cancellation():
    result = run(
        [
            ("h", "body", None, [0]),
            ("x", "body", None, [0]),
            ("h", "body", None, [0]),
        ],
        passes=["cancel"],
    )
    assert len(gate_sequence(result.module)) == 3


# --------------------------------------------------------------------------
# rotation fusion
# --------------------------------------------------------------------------


def test_rotations_fuse():
    result = run(
        [("rz", "body", 0.3, [0]), ("rz", "body", 0.4, [0])],
        passes=["merge-rotations"],
    )
    assert gate_sequence(result.module) == [("rz", "body")]
    assert angles(result.module)[0] == pytest.approx(0.7)


def test_rotations_on_different_axes_do_not_fuse():
    result = run(
        [("rz", "body", 0.3, [0]), ("rx", "body", 0.4, [0])],
        passes=["merge-rotations"],
    )
    assert len(gate_sequence(result.module)) == 2


def test_pair_rotations_fuse():
    result = run(
        [("rzz", "body", 0.3, [0, 1]), ("rzz", "body", 0.4, [0, 1])],
        passes=["merge-rotations"],
    )
    assert gate_sequence(result.module) == [("rzz", "body")]
    assert angles(result.module)[0] == pytest.approx(0.7)


def test_adjoint_rotation_fuses_with_negated_angle():
    result = run(
        [("rz", "body", 1.0, [0]), ("rz", "adj", 0.25, [0])],
        passes=["merge-rotations"],
    )
    assert angles(result.module)[0] == pytest.approx(0.75)


def test_symbolic_angles_are_left_alone():
    ir = make_ir([("rz", "body", 0.5, [0])], num_qubits=1).replace(
        "double 5.0000000000000000e-01", "double %theta"
    )
    ir = ir.replace("define void @main() #0 {", "define void @main(double %theta) #0 {")
    result = optimize(ir, level=3)
    assert "double %theta" in result.to_ll()


# --------------------------------------------------------------------------
# commutation
# --------------------------------------------------------------------------


def test_t_gates_fuse_through_a_cnot_control():
    result = run(
        [
            ("t", "body", None, [0]),
            ("cnot", "body", None, [0, 1]),
            ("t", "body", None, [0]),
        ],
        level=3,
    )
    assert sorted(gate_sequence(result.module)) == [("cnot", "body"), ("s", "body")]


def test_cnots_cancel_through_a_commuting_gate():
    # Only the commutation pass can do this one: the T on the control leg
    # commutes with both CNOTs, so they meet and annihilate.
    result = run(
        [
            ("cnot", "body", None, [0, 1]),
            ("t", "body", None, [0]),
            ("cnot", "body", None, [0, 1]),
        ],
        passes=["commute"],
    )
    assert gate_sequence(result.module) == [("t", "body")]


def test_pair_rotations_fuse_through_a_commuting_gate():
    result = run(
        [
            ("rzz", "body", 0.3, [0, 1]),
            ("z", "body", None, [0]),
            ("rzz", "body", 0.4, [0, 1]),
        ],
        passes=["commute"],
    )
    assert gate_sequence(result.module) == [("z", "body"), ("rzz", "body")]
    assert angles(result.module)[0] == pytest.approx(0.7)


def test_x_gates_cancel_through_a_cnot_target():
    result = run(
        [
            ("x", "body", None, [1]),
            ("cnot", "body", None, [0, 1]),
            ("x", "body", None, [1]),
        ],
        passes=["commute"],
    )
    assert gate_sequence(result.module) == [("cnot", "body")]


def test_x_does_not_commute_through_a_cnot_control():
    result = run(
        [
            ("x", "body", None, [0]),
            ("cnot", "body", None, [0, 1]),
            ("x", "body", None, [0]),
        ],
        passes=["commute"],
    )
    assert len(gate_sequence(result.module)) == 3


def test_h_blocks_commutation():
    result = run(
        [
            ("t", "body", None, [0]),
            ("h", "body", None, [0]),
            ("t", "body", None, [0]),
        ],
        passes=["commute"],
    )
    assert len(gate_sequence(result.module)) == 3


def test_disjoint_gates_never_block():
    result = run(
        [
            ("x", "body", None, [0]),
            ("h", "body", None, [1]),
            ("x", "body", None, [0]),
        ],
        passes=["commute"],
    )
    assert gate_sequence(result.module) == [("h", "body")]


# --------------------------------------------------------------------------
# Clifford+T
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, [("t", "body")]),
        (2, [("s", "body")]),
        (3, [("s", "body"), ("t", "body")]),
        (4, [("z", "body")]),
        (5, [("z", "body"), ("t", "body")]),
        (6, [("s", "adj")]),
        (7, [("t", "adj")]),
        (8, []),
    ],
)
def test_t_ladder(count, expected):
    result = run([("t", "body", None, [0])] * count, num_qubits=1, level=3)
    assert gate_sequence(result.module) == expected


@pytest.mark.parametrize(
    "angle,expected",
    [
        (PI / 4, [("t", "body")]),
        (PI / 2, [("s", "body")]),
        (PI, [("z", "body")]),
        (2 * PI, []),
        (-PI / 4, [("t", "adj")]),
    ],
)
def test_exact_rotations_become_clifford_t(angle, expected):
    result = run([("rz", "body", angle, [0])], num_qubits=1, passes=["clifford-t"])
    assert gate_sequence(result.module) == expected


def test_arbitrary_rotation_is_left_as_a_rotation():
    result = run([("rz", "body", 0.37, [0])], num_qubits=1, level=3)
    assert gate_sequence(result.module) == [("rz", "body")]
    assert angles(result.module)[0] == pytest.approx(0.37)


def test_mixed_diagonal_run_collapses():
    result = run(
        [
            ("z", "body", None, [0]),
            ("s", "body", None, [0]),
            ("t", "body", None, [0]),
            ("rz", "body", PI / 8, [0]),
        ],
        num_qubits=1,
        level=3,
    )
    # pi + pi/2 + pi/4 + pi/8 = 15pi/8, not a multiple of pi/4.
    assert gate_sequence(result.module) == [("rz", "body")]
    assert angles(result.module)[0] == pytest.approx(15 * PI / 8 - 2 * PI)


def test_x_axis_run_collapses():
    result = run(
        [("x", "body", None, [0]), ("rx", "body", PI, [0])], num_qubits=1, level=3
    )
    assert gate_sequence(result.module) == []


def test_identity_gate_is_removed():
    result = run([("i", "body", None, [0])], num_qubits=1, level=3)
    assert gate_sequence(result.module) == []


# --------------------------------------------------------------------------
# policy switches
# --------------------------------------------------------------------------


def test_strict_gateset_does_not_introduce_new_gates():
    gates = [("t", "body", None, [0])] * 2
    auto = run(gates, num_qubits=1, level=3, gateset="auto")
    strict = run(gates, num_qubits=1, level=3, gateset="strict")
    assert gate_sequence(auto.module) == [("s", "body")]
    assert gate_sequence(strict.module) == [("t", "body"), ("t", "body")]


def test_preserve_global_phase_rejects_phase_changing_rewrites():
    gates = [("rz", "body", PI, [0])]
    free = run(gates, num_qubits=1, level=3)
    kept = run(gates, num_qubits=1, level=3, preserve_global_phase=True)
    assert gate_sequence(free.module) == [("z", "body")]
    assert free.global_phase == pytest.approx(-PI / 2)
    assert gate_sequence(kept.module) == [("rz", "body")]
    assert kept.global_phase == 0.0


def test_phase_exact_rewrites_still_apply_under_preserve():
    result = run(
        [("t", "body", None, [0])] * 2,
        num_qubits=1,
        level=3,
        preserve_global_phase=True,
    )
    assert gate_sequence(result.module) == [("s", "body")]
    assert result.global_phase == 0.0


def test_o0_changes_nothing():
    ir = make_ir([("h", "body", None, [0])] * 4, num_qubits=1)
    assert optimize(ir, level=0).to_ll() == ir


def test_disable_removes_a_pass():
    gates = [("t", "body", None, [0])] * 2
    result = run(gates, num_qubits=1, level=3, disable=["clifford-t", "commute"])
    assert gate_sequence(result.module) == [("t", "body"), ("t", "body")]
