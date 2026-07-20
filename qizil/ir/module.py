"""Structural model of a QIR module.

Fidelity rule: every line Qizil does not deliberately rewrite is emitted back
byte-for-byte.  Instructions keep their original text and are only re-rendered
after a pass marks them dirty, which is what keeps classical control flow,
metadata, attributes and debug info intact through an optimization run.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from .gates import GateSpec
from .values import Operand, PointerRef

__all__ = [
    "InstKind",
    "QuantumOp",
    "Instruction",
    "BasicBlock",
    "Function",
    "Module",
    "RawChunk",
]


class InstKind(Enum):
    #: A recognized QIS gate / measurement / reset call.
    QUANTUM = "quantum"
    #: Touches quantum state in a way Qizil does not model. Acts as a full
    #: scheduling barrier: nothing is moved across it.
    BARRIER = "barrier"
    #: Purely classical computation. Never moved, never a barrier.
    CLASSICAL = "classical"
    #: Block terminator (br / ret / switch / unreachable / ...).
    TERMINATOR = "terminator"
    #: Blank line or comment.
    TRIVIA = "trivia"


TERMINATOR_OPCODES = frozenset(
    {"ret", "br", "switch", "indirectbr", "invoke", "resume", "unreachable", "callbr"}
)


@dataclass
class QuantumOp:
    """The parsed content of a recognized ``__quantum__qis__*`` call."""

    func: str
    base: str
    functor: str
    spec: GateSpec
    args: list[Operand]
    qubits: tuple[PointerRef, ...] = ()
    results: tuple[PointerRef, ...] = ()
    angles: tuple[float | None, ...] = ()
    angle_texts: tuple[str, ...] = ()

    @property
    def is_gate(self) -> bool:
        return self.spec.kind == "gate"

    def qubit_keys(self) -> tuple:
        return tuple(q.key for q in self.qubits)

    def describe(self) -> str:
        params = ",".join(
            f"{a:.6g}" if a is not None else t
            for a, t in zip(self.angles, self.angle_texts)
        )
        qubits = ",".join(str(q) for q in self.qubits)
        head = self.base if self.functor == "body" else f"{self.base}†"
        return f"{head}({params}{';' if params else ''}{qubits})"


@dataclass
class Instruction:
    """One line of a basic block."""

    text: str
    kind: InstKind
    indent: str = "  "
    assign: str | None = None
    opcode: str | None = None
    op: QuantumOp | None = None
    uses: frozenset[str] = frozenset()
    # Pieces of a ``call`` instruction, used to re-render a rewritten call.
    call_prefix: str = ""
    call_suffix: str = ""
    dirty: bool = False
    #: Provenance note for the optimization report.
    origin: str | None = None

    def render(self) -> str:
        if not self.dirty:
            return self.text
        if self.op is not None:
            args = ", ".join(a.render() for a in self.op.args)
            return f"{self.call_prefix}@{self.op.func}({args}){self.call_suffix}"
        return self.text

    def mark_dirty(self) -> None:
        self.dirty = True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.op is not None:
            return f"<Inst {self.op.describe()}>"
        return f"<Inst {self.kind.value} {self.text.strip()[:48]!r}>"


@dataclass
class BasicBlock:
    label: str | None
    header: str | None  # verbatim label line, if any
    instructions: list[Instruction] = field(default_factory=list)

    def quantum_ops(self) -> Iterator[tuple[int, Instruction]]:
        for i, inst in enumerate(self.instructions):
            if inst.kind is InstKind.QUANTUM:
                yield i, inst

    def render(self) -> list[str]:
        out: list[str] = []
        if self.header is not None:
            out.append(self.header)
        out.extend(inst.render() for inst in self.instructions)
        return out


@dataclass
class Function:
    name: str
    header: str  # verbatim "define ... {" line
    blocks: list[BasicBlock] = field(default_factory=list)
    footer: str = "}"
    is_entry_point: bool = False

    def render(self) -> list[str]:
        out = [self.header]
        for block in self.blocks:
            out.extend(block.render())
        out.append(self.footer)
        return out

    def instructions(self) -> Iterator[Instruction]:
        for block in self.blocks:
            yield from block.instructions


@dataclass
class RawChunk:
    """Top-level text preserved verbatim (types, globals, declares, metadata)."""

    lines: list[str] = field(default_factory=list)

    def render(self) -> list[str]:
        return list(self.lines)


@dataclass
class Module:
    items: list[RawChunk | Function] = field(default_factory=list)
    source_name: str = "<memory>"
    #: Phase dropped by rewrites, in radians: the original circuit equals
    #: ``exp(i * global_phase)`` times the gates now in the module.  Global
    #: phase is physically unobservable, but it is tracked (and reported)
    #: rather than silently discarded.
    global_phase: float = 0.0
    #: SSA names known to hold a freshly allocated qubit.
    alloc_names: set[str] = field(default_factory=set)

    # -- access ---------------------------------------------------------
    @property
    def functions(self) -> list[Function]:
        return [i for i in self.items if isinstance(i, Function)]

    def blocks(self) -> Iterator[tuple[Function, BasicBlock]]:
        for fn in self.functions:
            for block in fn.blocks:
                yield fn, block

    def declared_gates(self) -> set[str]:
        """Names of ``__quantum__*`` functions declared or defined in the module."""
        names: set[str] = set()
        for item in self.items:
            if isinstance(item, RawChunk):
                for line in item.lines:
                    m = re.match(r"^declare\s+.*?@([\w.$]+)\s*\(", line)
                    if m:
                        names.add(m.group(1))
            else:
                names.add(item.name)
        return names

    # -- mutation -------------------------------------------------------
    def ensure_declaration(self, func: str, signature: str) -> None:
        """Add ``declare <signature> @func`` if the module lacks it.

        ``signature`` is ``"<ret> (<params>)"``; the declaration is inserted
        next to the module's existing declares so the output keeps its shape.
        """
        if func in self.declared_gates():
            return
        ret, _, params = signature.partition(" ")
        line = f"declare {ret} @{func}{params.strip()}"

        last_declare: tuple[int, int] | None = None
        first_metadata: tuple[int, int] | None = None
        for ci, item in enumerate(self.items):
            if not isinstance(item, RawChunk):
                continue
            for li, text in enumerate(item.lines):
                if text.startswith("declare "):
                    last_declare = (ci, li)
                elif first_metadata is None and text.startswith("!"):
                    first_metadata = (ci, li)
        if last_declare is not None:
            ci, li = last_declare
            chunk = self.items[ci].lines
            # Match the surrounding style: QIR emitters usually leave a blank
            # line between declarations.
            spaced = li > 0 and chunk[li - 1].strip() == ""
            chunk[li + 1 : li + 1] = ["", line] if spaced else [line]
            return
        if first_metadata is not None:
            ci, li = first_metadata
            self.items[ci].lines.insert(li, line)
            self.items[ci].lines.insert(li + 1, "")
            return
        self.items.append(RawChunk(["", line]))

    # -- output ---------------------------------------------------------
    def to_ll(self) -> str:
        lines: list[str] = []
        for item in self.items:
            lines.extend(item.render())
        text = "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
        return text
