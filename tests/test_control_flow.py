"""The critical constraint: classical control flow must survive untouched.

Quantum rewrites are only allowed to move gates past instructions Qizil can
prove are unaffected.  Measurements, runtime calls, unmodelled quantum
operations, block boundaries and classical code are all fences.
"""

from __future__ import annotations

import os

import pytest

from conftest import EXAMPLES, make_ir
from qizil.api import optimize, parse
from qizil.ir.module import InstKind


def gates_of(module) -> list[str]:
    return [
        inst.op.base
        for _fn, block in module.blocks()
        for inst in block.instructions
        if inst.kind is InstKind.QUANTUM
    ]


def load_example(name: str) -> str:
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return fh.read()


def test_basic_block_structure_is_preserved():
    result = optimize(load_example("adaptive_branch.ll"), level=3)
    labels = [b.label for f in result.module.functions for b in f.blocks]
    assert labels == ["entry", "then", "else", "join"]


def test_branch_and_classical_instructions_are_untouched():
    text = load_example("adaptive_branch.ll")
    out = optimize(text, level=3).to_ll()
    for line in (
        "  %0 = call i1 @__quantum__qis__read_result__body(%Result* null)",
        "  br i1 %0, label %then, label %else",
        "  br label %join",
        "  ret void",
    ):
        assert line in out, line


def test_gates_do_not_cancel_across_a_measurement():
    ir = """%Qubit = type opaque
%Result = type opaque

define void @main() {
entry:
  call void @__quantum__qis__h__body(%Qubit* null)
  call void @__quantum__qis__mz__body(%Qubit* null, %Result* null)
  call void @__quantum__qis__h__body(%Qubit* null)
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
declare void @__quantum__qis__mz__body(%Qubit*, %Result*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["h", "mz", "h"]


def test_gates_on_other_qubits_still_optimize_around_a_measurement():
    ir = """%Qubit = type opaque
%Result = type opaque

define void @main() {
entry:
  call void @__quantum__qis__h__body(%Qubit* inttoptr (i64 1 to %Qubit*))
  call void @__quantum__qis__mz__body(%Qubit* null, %Result* null)
  call void @__quantum__qis__h__body(%Qubit* inttoptr (i64 1 to %Qubit*))
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
declare void @__quantum__qis__mz__body(%Qubit*, %Result*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["mz"]


def test_runtime_calls_are_scheduling_barriers():
    ir = """%Qubit = type opaque

define void @main() {
entry:
  %q = call %Qubit* @__quantum__rt__qubit_allocate()
  call void @__quantum__qis__h__body(%Qubit* %q)
  call void @__quantum__rt__qubit_release(%Qubit* %q)
  call void @__quantum__qis__h__body(%Qubit* %q)
  ret void
}

declare %Qubit* @__quantum__rt__qubit_allocate()
declare void @__quantum__rt__qubit_release(%Qubit*)
declare void @__quantum__qis__h__body(%Qubit*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["h", "h"]


def test_unmodelled_quantum_call_blocks_rewrites():
    ir = """%Qubit = type opaque

define void @main() {
entry:
  call void @__quantum__qis__h__body(%Qubit* null)
  call void @__quantum__qis__mystery__body(%Qubit* null)
  call void @__quantum__qis__h__body(%Qubit* null)
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
declare void @__quantum__qis__mystery__body(%Qubit*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["h", "h"]


def test_controlled_functor_blocks_rewrites():
    ir = """%Qubit = type opaque
%Array = type opaque

define void @main() {
entry:
  call void @__quantum__qis__t__body(%Qubit* null)
  call void @__quantum__qis__x__ctl(%Array* null, %Qubit* null)
  call void @__quantum__qis__t__body(%Qubit* null)
  ret void
}

declare void @__quantum__qis__t__body(%Qubit*)
declare void @__quantum__qis__x__ctl(%Array*, %Qubit*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["t", "t"]


def test_opaque_qubit_pointer_blocks_rewrites():
    # A qubit loaded from an array may alias anything, so nothing may move.
    ir = """%Qubit = type opaque

define void @main(%Qubit** %qs) {
entry:
  %q = load %Qubit*, %Qubit** %qs
  call void @__quantum__qis__h__body(%Qubit* null)
  call void @__quantum__qis__h__body(%Qubit* %q)
  call void @__quantum__qis__h__body(%Qubit* null)
  ret void
}

declare void @__quantum__qis__h__body(%Qubit*)
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == ["h", "h", "h"]


def test_non_entry_functions_are_optimized_too():
    ir = """%Qubit = type opaque

define void @helper(%Qubit* %q) {
entry:
  call void @__quantum__qis__x__body(%Qubit* null)
  call void @__quantum__qis__x__body(%Qubit* null)
  ret void
}

define void @main() #0 {
entry:
  call void @helper(%Qubit* null)
  ret void
}

declare void @__quantum__qis__x__body(%Qubit*)

attributes #0 = { "entry_point" }
"""
    result = optimize(ir, level=3)
    assert gates_of(result.module) == []
    assert "define void @helper(%Qubit* %q) {" in result.to_ll()
    assert "  call void @helper(%Qubit* null)" in result.to_ll()


def test_module_metadata_and_attributes_survive():
    text = load_example("bell_redundant.ll")
    out = optimize(text, level=3).to_ll()
    for line in (
        '!0 = !{i32 1, !"qir_major_version", i32 1}',
        'attributes #1 = { "irreversible" }',
        "%Result = type opaque",
        'source_filename = "bell_redundant"',
    ):
        assert line in out, line


def test_unoptimizable_module_is_returned_verbatim():
    text = load_example("adaptive_branch.ll")
    assert optimize(text, level=0).to_ll() == text


def test_new_gates_get_declarations():
    result = optimize(make_ir([("t", "body", None, [0])] * 2, 1), level=3)
    out = result.to_ll()
    assert "declare void @__quantum__qis__s__body(%Qubit*)" in out
    assert out.count("@__quantum__qis__s__body") == 2  # declaration + use


@pytest.mark.parametrize(
    "name",
    [
        "bell_redundant.ll",
        "commuting_t.ll",
        "adaptive_branch.ll",
        "dynamic_qubits.ll",
    ],
)
def test_examples_keep_their_instruction_fences(name):
    text = load_example(name)
    before = parse(text)
    after = optimize(text, level=3).module
    fences_before = [
        " ".join(i.text.split())
        for _f, b in before.blocks()
        for i in b.instructions
        if i.kind in (InstKind.BARRIER, InstKind.CLASSICAL, InstKind.TERMINATOR)
    ]
    fences_after = [
        " ".join(i.text.split())
        for _f, b in after.blocks()
        for i in b.instructions
        if i.kind in (InstKind.BARRIER, InstKind.CLASSICAL, InstKind.TERMINATOR)
    ]
    assert fences_before == fences_after
