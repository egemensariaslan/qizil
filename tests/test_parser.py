"""Parser fidelity: what goes in must come out, byte for byte."""

from __future__ import annotations

import glob
import math
import os

import pytest

from conftest import EXAMPLES, make_ir
from qizil.ir.module import InstKind
from qizil.ir.parser import ParseError, parse_ll
from qizil.ir.values import (
    RefKind,
    format_double,
    parse_double,
    parse_operand,
    split_args,
)

EXAMPLE_FILES = sorted(glob.glob(os.path.join(EXAMPLES, "*.ll")))


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: os.path.basename(p))
def test_roundtrip_is_byte_exact(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert parse_ll(text).to_ll() == text


def test_bitcode_input_is_rejected_clearly():
    with pytest.raises(ParseError, match="bitcode"):
        parse_ll("BC\xc0\xde\0\0\0\0garbage")


def test_hex_double_literals():
    # LLVM prints doubles it cannot render exactly as raw IEEE-754 bits.
    assert parse_double("0x3FE921FB54442D18") == pytest.approx(math.pi / 4)
    assert parse_double("1.500000e+00") == 1.5
    assert parse_double("%theta") is None
    assert parse_double("0xK4000C90FDAA22168C000") is None


def test_format_double_roundtrips():
    for value in (0.1, math.pi, -1e-17, 1e300, 0.0):
        assert parse_double(format_double(value)) == value


def test_split_args_respects_nesting():
    text = "%Qubit* inttoptr (i64 1 to %Qubit*), double 1.0, %Result* null"
    assert split_args(text) == [
        "%Qubit* inttoptr (i64 1 to %Qubit*)",
        "double 1.0",
        "%Result* null",
    ]


def test_parse_operand_strips_param_attrs():
    op = parse_operand("%Qubit* nonnull inttoptr (i64 3 to %Qubit*)")
    assert op.type == "%Qubit*"
    assert op.attrs == "nonnull"
    assert op.value == "inttoptr (i64 3 to %Qubit*)"


def test_qubit_reference_classification():
    module = parse_ll(
        make_ir([("h", "body", None, [0]), ("h", "body", None, [2])], num_qubits=3)
    )
    ops = [
        inst.op
        for _fn, block in module.blocks()
        for inst in block.instructions
        if inst.kind is InstKind.QUANTUM
    ]
    assert [op.qubits[0].id for op in ops] == [0, 2]
    assert all(op.qubits[0].kind is RefKind.STATIC for op in ops)


def test_dynamic_qubits_are_alloc_refs():
    module = parse_ll(
        """%Qubit = type opaque

define void @main() {
entry:
  %q = call %Qubit* @__quantum__rt__qubit_allocate()
  call void @__quantum__qis__h__body(%Qubit* %q)
  ret void
}

declare %Qubit* @__quantum__rt__qubit_allocate()
declare void @__quantum__qis__h__body(%Qubit*)
"""
    )
    block = module.functions[0].blocks[0]
    kinds = [i.kind for i in block.instructions]
    assert kinds[0] is InstKind.BARRIER  # the runtime allocation
    assert kinds[1] is InstKind.QUANTUM
    assert block.instructions[1].op.qubits[0].kind is RefKind.ALLOC


def test_unknown_gate_stays_opaque():
    module = parse_ll(
        """%Qubit = type opaque

define void @main() {
entry:
  call void @__quantum__qis__mystery__body(%Qubit* null)
  ret void
}

declare void @__quantum__qis__mystery__body(%Qubit*)
"""
    )
    block = module.functions[0].blocks[0]
    assert block.instructions[0].kind is InstKind.BARRIER


def test_controlled_functor_is_opaque():
    module = parse_ll(
        """%Qubit = type opaque
%Array = type opaque

define void @main() {
entry:
  call void @__quantum__qis__x__ctl(%Array* null, %Qubit* null)
  ret void
}

declare void @__quantum__qis__x__ctl(%Array*, %Qubit*)
"""
    )
    assert module.functions[0].blocks[0].instructions[0].kind is InstKind.BARRIER


def test_blocks_and_entry_point_detection():
    with open(os.path.join(EXAMPLES, "adaptive_branch.ll"), encoding="utf-8") as fh:
        module = parse_ll(fh.read())
    fn = module.functions[0]
    assert fn.is_entry_point
    assert [b.label for b in fn.blocks] == ["entry", "then", "else", "join"]


def test_ensure_declaration_is_idempotent():
    module = parse_ll(make_ir([("h", "body", None, [0])], num_qubits=1))
    module.ensure_declaration("__quantum__qis__s__body", "void (%Qubit*)")
    module.ensure_declaration("__quantum__qis__s__body", "void (%Qubit*)")
    assert module.to_ll().count("@__quantum__qis__s__body") == 1
