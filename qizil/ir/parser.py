"""Line-oriented parser for textual LLVM IR carrying QIR.

Qizil is not a general LLVM frontend and does not try to be one: it recovers
exactly the structure it needs to reason about quantum instructions (functions,
basic blocks, calls into ``__quantum__qis__*``) and keeps everything else as
opaque text.  That is what lets an optimized module round-trip through
``llvm-as``/``pyqir`` unchanged apart from the gates we meant to touch.
"""

from __future__ import annotations

import re

from .gates import lookup, split_qis_name
from .module import (
    TERMINATOR_OPCODES,
    BasicBlock,
    Function,
    InstKind,
    Instruction,
    Module,
    QuantumOp,
    RawChunk,
)
from .values import (
    PointerRef,
    RefKind,
    parse_double,
    parse_operand,
    parse_pointer,
    split_args,
)

__all__ = ["parse_ll", "parse_file", "ParseError"]


class ParseError(ValueError):
    """Raised when the input is not textual LLVM IR we can work with."""


_DEFINE_RE = re.compile(r"^define\b")
_FUNC_NAME_RE = re.compile(r'@(?P<name>[\w.$]+|"[^"]*")\s*\(')
_LABEL_RE = re.compile(r'^(?P<label>[-\w.$]+|"[^"]*"):(?:\s|$)')
_OLD_LABEL_RE = re.compile(r"^;\s*<label>:(?P<label>[-\w.$]+):?\s*$")
_ASSIGN_RE = re.compile(r'^(?P<name>%[\w.$]+|%"[^"]*")\s*=\s*(?P<rest>.*)$')
_OPCODE_RE = re.compile(r"^([a-z][a-z0-9_]*)")
_CALLEE_RE = re.compile(r'@(?P<name>[\w.$]+|"[^"]*")\s*\(')
_TYPEDEF_RE = re.compile(r'^(%[\w.$]+|%"[^"]*")\s*=\s*type\b')
_ALLOC_RE = re.compile(
    r'^\s*(?P<name>%[\w.$]+|%"[^"]*")\s*=\s*(?:tail\s+)?call\s+'
    r"%Qubit\s*\*\s*@__quantum__rt__qubit_allocate\s*\("
)
_ATTR_GROUP_RE = re.compile(r"^attributes\s+(#\d+)\s*=\s*\{(?P<body>.*)\}\s*$")

_QUANTUM_TYPES = ("%Qubit", "%Result")


# --------------------------------------------------------------------------
# Top level
# --------------------------------------------------------------------------


def parse_file(path: str) -> Module:
    with open(path, encoding="utf-8") as fh:
        module = parse_ll(fh.read())
    module.source_name = path
    return module


def parse_ll(text: str) -> Module:
    """Parse textual LLVM IR into a :class:`Module`."""
    if "\0" in text[:4096]:
        raise ParseError(
            "input looks like bitcode, not textual IR "
            "(install the 'bitcode' extra to read .bc files)"
        )
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline is re-added on emit

    module = Module()
    type_names = {
        m.group(1) for m in (_TYPEDEF_RE.match(line) for line in lines) if m is not None
    }
    type_names.update({"%Qubit", "%Result", "%Array", "%Tuple", "%Callable", "%String"})

    chunk = RawChunk()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _DEFINE_RE.match(line):
            if chunk.lines:
                module.items.append(chunk)
                chunk = RawChunk()
            fn, i = _parse_function(lines, i, type_names)
            module.items.append(fn)
            continue
        chunk.lines.append(line)
        i += 1
    if chunk.lines:
        module.items.append(chunk)

    _mark_entry_points(module)
    return module


def _mark_entry_points(module: Module) -> None:
    groups: dict[str, str] = {}
    for item in module.items:
        if isinstance(item, RawChunk):
            for line in item.lines:
                m = _ATTR_GROUP_RE.match(line)
                if m:
                    groups[m.group(1)] = m.group("body")
    for fn in module.functions:
        header = fn.header
        if "entry_point" in header:
            fn.is_entry_point = True
            continue
        for ref, body in groups.items():
            if re.search(rf"{re.escape(ref)}\b", header) and "entry_point" in body:
                fn.is_entry_point = True
                break


# --------------------------------------------------------------------------
# Functions and blocks
# --------------------------------------------------------------------------


def _parse_function(
    lines: list[str], start: int, type_names: set[str]
) -> tuple[Function, int]:
    header_parts = [lines[start]]
    i = start
    while "{" not in header_parts[-1] and i + 1 < len(lines):
        i += 1
        header_parts.append(lines[i])
    i += 1
    header = "\n".join(header_parts)
    m = _FUNC_NAME_RE.search(header)
    name = m.group("name") if m else f"<anon@{start}>"

    body_start = i
    depth = 1
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if i >= len(lines):
        raise ParseError(f"unterminated function body for @{name}")
    body = lines[body_start:i]
    footer = lines[i]
    i += 1

    fn = Function(name=name, header=header, footer=footer)
    fn.blocks = _parse_blocks(body, type_names)
    return fn, i


def _parse_blocks(body: list[str], type_names: set[str]) -> list[BasicBlock]:
    alloc_names = {
        m.group("name") for m in (_ALLOC_RE.match(line) for line in body) if m is not None
    }
    blocks: list[BasicBlock] = []
    current = BasicBlock(label=None, header=None)
    i = 0
    while i < len(body):
        line = body[i]
        stripped = line.strip()
        label = None
        if not line[:1].isspace() and stripped:
            m = _LABEL_RE.match(stripped)
            if m:
                label = m.group("label")
        if label is None:
            m = _OLD_LABEL_RE.match(stripped)
            if m:
                label = m.group("label")
        if label is not None:
            if current.instructions or current.header is not None:
                blocks.append(current)
            current = BasicBlock(label=label, header=line)
            i += 1
            continue

        text, consumed = _join_instruction(body, i)
        current.instructions.append(_classify(text, alloc_names, type_names))
        i += consumed
    blocks.append(current)
    return blocks


def _join_instruction(body: list[str], i: int) -> tuple[str, int]:
    """Join continuation lines (e.g. a multi-line ``switch``) into one text."""
    text = body[i]
    consumed = 1
    if text.count("[") > text.count("]"):
        while i + consumed < len(body) and text.count("[") > text.count("]"):
            text += "\n" + body[i + consumed]
            consumed += 1
    return text, consumed


# --------------------------------------------------------------------------
# Instruction classification
# --------------------------------------------------------------------------


def _classify(text: str, alloc_names: set[str], type_names: set[str]) -> Instruction:
    stripped = text.strip()
    indent = text[: len(text) - len(text.lstrip())] or "  "
    if not stripped or stripped.startswith(";"):
        return Instruction(text=text, kind=InstKind.TRIVIA, indent=indent)

    assign = None
    rest = stripped
    m = _ASSIGN_RE.match(stripped)
    if m:
        assign = m.group("name")
        rest = m.group("rest")
    opcode_match = _OPCODE_RE.match(rest)
    opcode = opcode_match.group(1) if opcode_match else None
    uses = _extract_uses(text, assign, type_names)

    if opcode in TERMINATOR_OPCODES and opcode != "invoke":
        return Instruction(
            text=text,
            kind=InstKind.TERMINATOR,
            indent=indent,
            assign=assign,
            opcode=opcode,
            uses=uses,
        )

    is_call = opcode in ("call", "invoke") or rest.startswith("tail call")
    if is_call:
        inst = _classify_call(text, indent, assign, opcode, rest, uses, alloc_names)
        if inst is not None:
            return inst
        return Instruction(
            text=text,
            kind=InstKind.BARRIER,
            indent=indent,
            assign=assign,
            opcode=opcode,
            uses=uses,
        )

    kind = (
        InstKind.BARRIER
        if any(t in text for t in _QUANTUM_TYPES)
        else InstKind.CLASSICAL
    )
    return Instruction(
        text=text,
        kind=kind,
        indent=indent,
        assign=assign,
        opcode=opcode,
        uses=uses,
    )


def _classify_call(
    text: str,
    indent: str,
    assign: str | None,
    opcode: str | None,
    rest: str,
    uses: frozenset[str],
    alloc_names: set[str],
) -> Instruction | None:
    parsed = _split_call(text)
    if parsed is None:
        return None
    prefix, func, args_text, suffix = parsed
    split = split_qis_name(func)
    if split is None:
        return None
    base, functor = split
    spec = lookup(base)
    if spec is None or functor not in ("body", "adj"):
        return None
    if functor == "adj":
        # The adjoint is only modelled where the algebra defines it: Hermitian
        # gates (adj == body), fixed 1-qubit gates, and rotations.
        if spec.kind != "gate":
            return None
        if not (spec.hermitian or spec.axis_gate is not None or spec.is_rotation):
            return None

    args = [parse_operand(a) for a in split_args(args_text)] if args_text.strip() else []
    qubits: list[PointerRef] = []
    results: list[PointerRef] = []
    angles: list[float | None] = []
    angle_texts: list[str] = []
    for arg in args:
        if "Qubit" in arg.type:
            qubits.append(parse_pointer(arg, alloc_names))
        elif "Result" in arg.type:
            results.append(parse_pointer(arg, alloc_names))
        elif arg.type == "double":
            angles.append(parse_double(arg.value))
            angle_texts.append(arg.value)
        else:
            return None  # unmodelled operand type -> stay opaque

    if len(qubits) != spec.num_qubits or len(angles) != spec.num_params:
        return None
    if spec.returns_result:
        if assign is None:
            return None
        results = [PointerRef(RefKind.ALLOC, "Result", name=assign, text=assign)]
    elif len(results) != spec.num_results:
        return None

    op = QuantumOp(
        func=func,
        base=base,
        functor=functor,
        spec=spec,
        args=args,
        qubits=tuple(qubits),
        results=tuple(results),
        angles=tuple(angles),
        angle_texts=tuple(angle_texts),
    )
    return Instruction(
        text=text,
        kind=InstKind.QUANTUM,
        indent=indent,
        assign=assign,
        opcode=opcode,
        op=op,
        uses=uses,
        call_prefix=prefix,
        call_suffix=suffix,
    )


def _split_call(text: str) -> tuple[str, str, str, str] | None:
    """Split a call into ``(prefix, callee, args, suffix)``.

    ``prefix`` covers everything up to (not including) the ``@`` of the callee,
    ``suffix`` everything after the closing parenthesis, so the two together
    preserve calling convention, attributes, ``!dbg`` metadata and indentation.
    """
    m = _CALLEE_RE.search(text)
    if m is None:
        return None
    open_paren = text.index("(", m.end("name"))
    depth = 0
    close = -1
    in_string = False
    for i in range(open_paren, len(text)):
        ch = text[i]
        if in_string:
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                close = i
                break
    if close < 0:
        return None
    return text[: m.start()], m.group("name"), text[open_paren + 1 : close], text[close + 1 :]


def _extract_uses(
    text: str, assign: str | None, type_names: set[str]
) -> frozenset[str]:
    names = set()
    for m in re.finditer(r'%(?:[\w.$]+|"[^"]*")', text):
        name = m.group(0)
        if name in type_names or name == assign:
            continue
        names.add(name)
    return frozenset(names)
