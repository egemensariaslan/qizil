"""Metrics, the dependency DAG, and resource estimation."""

from __future__ import annotations

import math
import os

import pytest

from conftest import EXAMPLES, make_ir
from qizil.analysis.estimator import QUBIT_PRESETS, SurfaceCode, estimate
from qizil.analysis.metrics import measure
from qizil.api import optimize, parse
from qizil.ir.dag import BlockDag, commutes
from qizil.ir.module import InstKind

PI = math.pi


def ops_of(module):
    return [
        inst.op
        for _f, b in module.blocks()
        for inst in b.instructions
        if inst.kind is InstKind.QUANTUM
    ]


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def test_metrics_counts():
    module = parse(
        make_ir(
            [
                ("h", "body", None, [0]),
                ("t", "body", None, [1]),
                ("rz", "body", 0.37, [0]),
                ("rz", "body", PI / 4, [1]),
                ("rz", "body", PI / 2, [1]),
                ("cnot", "body", None, [0, 1]),
            ],
            num_qubits=2,
            measure=True,
        )
    )
    m = measure(module)
    assert m.qubits == 2
    assert m.gates == 6
    assert m.two_qubit_gates == 1
    assert m.t_gates == 1
    assert m.quarter_rotations == 1  # rz(pi/4)
    assert m.arbitrary_rotations == 1  # rz(0.37)
    assert m.clifford_gates == 3  # h, cnot, rz(pi/2)
    assert m.measurements == 2
    assert m.t_count == 2


def test_depth_ignores_independent_qubits():
    module = parse(
        make_ir(
            [("h", "body", None, [0]), ("h", "body", None, [1])], num_qubits=2
        )
    )
    assert measure(module).depth == 1


def test_depth_counts_dependent_chains():
    module = parse(make_ir([("h", "body", None, [0])] * 4, num_qubits=1))
    assert measure(module).depth == 4


def test_required_num_qubits_attribute_is_honoured():
    module = parse(make_ir([("h", "body", None, [0])], num_qubits=5))
    assert measure(module).qubits == 5


# --------------------------------------------------------------------------
# DAG
# --------------------------------------------------------------------------


def test_dag_edges_follow_qubit_dependencies():
    module = parse(
        make_ir(
            [
                ("h", "body", None, [0]),
                ("h", "body", None, [1]),
                ("cnot", "body", None, [0, 1]),
            ],
            num_qubits=2,
        )
    )
    dag = BlockDag(module.functions[0].blocks[0])
    assert dag.nodes[0].preds == set()
    assert dag.nodes[1].preds == set()
    assert dag.nodes[2].preds == {0, 1}
    assert dag.depth() == 2


def test_barrier_orders_everything():
    module = parse(
        """%Qubit = type opaque

define void @main() {
entry:
  call void @__quantum__qis__h__body(%Qubit* null)
  call void @__quantum__rt__qubit_release(%Qubit* null)
  call void @__quantum__qis__h__body(%Qubit* inttoptr (i64 1 to %Qubit*))
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
declare void @__quantum__rt__qubit_release(%Qubit*)
"""
    )
    dag = BlockDag(module.functions[0].blocks[0])
    assert dag.nodes[1].universal
    assert dag.nodes[1].preds == {0}
    assert dag.nodes[2].preds == {1}


def test_dot_output_is_wellformed():
    module = parse(make_ir([("h", "body", None, [0])], num_qubits=1))
    dot = BlockDag(module.functions[0].blocks[0]).to_dot("x")
    assert dot.startswith('digraph "x"')
    assert dot.rstrip().endswith("}")


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (("t", [0]), ("cnot", [0, 1]), True),  # diagonal on the control leg
        (("x", [1]), ("cnot", [0, 1]), True),  # X on the target leg
        (("x", [0]), ("cnot", [0, 1]), False),  # X on the control leg
        (("t", [1]), ("cnot", [0, 1]), False),  # diagonal on the target leg
        (("h", [0]), ("h", [0]), True),  # identical operations
        (("h", [0]), ("t", [0]), False),
        (("h", [0]), ("t", [1]), True),  # disjoint
        (("z", [0]), ("cz", [0, 1]), True),
        (("swap", [0, 1]), ("z", [0]), False),
    ],
)
def test_commutation_rules(a, b, expected):
    gates = [(a[0], "body", None, a[1]), (b[0], "body", None, b[1])]
    module = parse(make_ir(gates, num_qubits=2))
    op_a, op_b = ops_of(module)
    assert commutes(op_a, op_b) is expected
    assert commutes(op_b, op_a) is expected


def test_measurement_never_commutes_on_its_qubit():
    module = parse(
        make_ir([("z", "body", None, [0])], num_qubits=1, measure=True)
    )
    op_z, op_mz = ops_of(module)
    assert commutes(op_z, op_mz) is False


# --------------------------------------------------------------------------
# estimator
# --------------------------------------------------------------------------


def test_estimate_shrinks_with_optimization():
    with open(os.path.join(EXAMPLES, "bell_redundant.ll"), encoding="utf-8") as fh:
        text = fh.read()
    result = optimize(text, level=3)
    comparison = result.estimates()
    assert comparison.before.t_states > comparison.after.t_states
    assert comparison.before.physical_qubits > comparison.after.physical_qubits
    assert comparison.to_dict()["runtime_ns"]["percent"] < 0


def test_arbitrary_rotations_dominate_the_t_budget():
    exact = parse(make_ir([("rz", "body", PI / 4, [0])], num_qubits=1))
    arbitrary = parse(make_ir([("rz", "body", 0.37, [0])], num_qubits=1))
    assert estimate(arbitrary).t_states > 10 * estimate(exact).t_states


def test_code_distance_grows_as_the_budget_tightens():
    module = parse(make_ir([("t", "body", None, [0])] * 20, num_qubits=1))
    loose = estimate(module, error_budget=1e-2)
    tight = estimate(module, error_budget=1e-9)
    assert tight.code_distance > loose.code_distance
    assert tight.physical_qubits > loose.physical_qubits


def test_slower_qubits_mean_longer_runtime():
    module = parse(make_ir([("t", "body", None, [0])] * 10, num_qubits=1))
    fast = estimate(module, qubit_params="qubit_gate_ns_e3")
    slow = estimate(module, qubit_params="qubit_gate_us_e3")
    assert slow.runtime_ns > fast.runtime_ns


def test_better_qubits_need_a_smaller_code():
    module = parse(make_ir([("t", "body", None, [0])] * 10, num_qubits=1))
    noisy = estimate(module, qubit_params="qubit_gate_ns_e3")
    clean = estimate(module, qubit_params="qubit_gate_ns_e4")
    assert clean.code_distance < noisy.code_distance


def test_unknown_qubit_params_are_rejected():
    module = parse(make_ir([("h", "body", None, [0])], num_qubits=1))
    with pytest.raises(ValueError, match="unknown qubit parameter set"):
        estimate(module, qubit_params="nope")


def test_surface_code_distance_formula():
    qec = SurfaceCode()
    # A logical error rate below the target is what selects the distance.
    d = qec.distance_for(1e-3, 1e-10)
    assert d % 2 == 1
    assert qec.logical_error_rate(1e-3, d) <= 1e-10
    assert qec.logical_error_rate(1e-3, d - 2) > 1e-10


def test_presets_are_ordered_sensibly():
    assert QUBIT_PRESETS["qubit_gate_ns_e4"].error_rate < QUBIT_PRESETS[
        "qubit_gate_ns_e3"
    ].error_rate
