"""Operand-level model for QIR values.

The optimizer only ever rewrites instructions whose operands it fully
understands.  Everything else stays byte-identical, so this module is
deliberately conservative: when a value cannot be classified it becomes
``RefKind.UNKNOWN``, which aliases every other reference and therefore acts as
a scheduling barrier.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "RefKind",
    "PointerRef",
    "Operand",
    "parse_double",
    "format_double",
    "split_args",
    "parse_operand",
    "TAU",
]

TAU = 2.0 * math.pi


class RefKind(Enum):
    """How much we know about a ``%Qubit*`` / ``%Result*`` operand."""

    #: A compile-time constant id: ``null`` or ``inttoptr (i64 N to %Qubit*)``.
    STATIC = "static"
    #: An SSA value produced by ``__quantum__rt__qubit_allocate`` — distinct
    #: from every other allocation, and from every other ALLOC name.
    ALLOC = "alloc"
    #: Anything else (function arguments, array loads, bitcasts).  Aliases
    #: everything; no instruction touching one is ever moved or removed.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PointerRef:
    """A reference to a qubit or a measurement result."""

    kind: RefKind
    pointee: str  # "Qubit" or "Result"
    id: int | None = None  # set for STATIC
    name: str | None = None  # set for ALLOC / UNKNOWN SSA values
    text: str = ""  # verbatim operand text

    @property
    def key(self) -> tuple:
        """Identity key. Only meaningful when :meth:`is_definite`."""
        if self.kind is RefKind.STATIC:
            return (self.pointee, "static", self.id)
        return (self.pointee, self.kind.value, self.name)

    def is_definite(self) -> bool:
        """True when this reference denotes one specific, knowable qubit."""
        return self.kind in (RefKind.STATIC, RefKind.ALLOC)

    def same_as(self, other: PointerRef) -> bool:
        """Definitely the *same* qubit (used to fuse / cancel gates)."""
        return (
            self.is_definite()
            and other.is_definite()
            and self.pointee == other.pointee
            and self.key == other.key
        )

    def may_alias(self, other: PointerRef) -> bool:
        """Possibly the same qubit (used to order gates).

        STATIC ids and ALLOC handles are reported as *possibly* aliasing:
        a module that mixes the base-profile static addressing scheme with
        dynamic allocation is unusual enough that we refuse to reason about it.
        """
        if self.pointee != other.pointee:
            return False
        if self.kind is RefKind.UNKNOWN or other.kind is RefKind.UNKNOWN:
            return True
        if self.kind is other.kind:
            return self.key == other.key
        return True

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        if self.kind is RefKind.STATIC:
            return f"{self.pointee.lower()}{self.id}"
        return f"{self.pointee.lower()}:{self.name or '?'}"


@dataclass(frozen=True)
class Operand:
    """One argument of a ``call`` instruction: type, parameter attrs, value."""

    type: str
    value: str
    attrs: str = ""

    def render(self) -> str:
        parts = [self.type]
        if self.attrs:
            parts.append(self.attrs)
        parts.append(self.value)
        return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Floating point literals
# --------------------------------------------------------------------------

_HEX_DOUBLE = re.compile(r"^0x([0-9A-Fa-f]{16})$")
_EXT_HEX = re.compile(r"^0x[KLMH]")


def parse_double(text: str) -> float | None:
    """Parse an LLVM ``double`` literal, or return ``None`` if not constant.

    LLVM prints doubles either in decimal or as ``0x`` + the 16 hex digits of
    the IEEE-754 bit pattern; both forms appear in QIR emitted by real
    frontends.  Extended formats (``0xK``/``0xL``/``0xM``/``0xH``) belong to
    other float types and are rejected.
    """
    text = text.strip()
    if not text:
        return None
    if _EXT_HEX.match(text):
        return None
    m = _HEX_DOUBLE.match(text)
    if m:
        return struct.unpack(">d", bytes.fromhex(m.group(1)))[0]
    try:
        value = float(text)
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def format_double(value: float) -> str:
    """Render a Python float as an LLVM ``double`` literal.

    ``%.17g`` round-trips every finite IEEE-754 double exactly, so the emitted
    text denotes precisely the value we computed.
    """
    if value == 0.0:
        # Keep ``-0.0`` distinguishable but avoid "-0.0" surprises downstream.
        return "0.000000e+00"
    text = f"{value:.17g}"
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


# --------------------------------------------------------------------------
# Argument list parsing
# --------------------------------------------------------------------------


def split_args(text: str) -> list[str]:
    """Split a call argument list on top-level commas."""
    args: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            current.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
        elif ch in "([{<":
            depth += 1
            current.append(ch)
        elif ch in ")]}>":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


# Parameter attributes that may sit between the type and the value.
_PARAM_ATTRS = {
    "nonnull",
    "noundef",
    "readonly",
    "readnone",
    "writeonly",
    "immarg",
    "nocapture",
    "inreg",
    "signext",
    "zeroext",
    "returned",
    "nofree",
    "dereferenceable",
    "dereferenceable_or_null",
    "align",
    "byval",
    "sret",
    "alignstack",
    "swiftself",
    "noalias",
}

_TYPE_RE = re.compile(
    r"""^\s*
    (?P<type>
        (?: %"[^"]*" | %[\w.$-]+ | \[[^\]]*\] | \{[^}]*\} | <[^>]*> | [\w]+ )
        \s* \**
    )
    \s*(?P<rest>.*)$
    """,
    re.VERBOSE | re.DOTALL,
)


def _strip_param_attrs(text: str) -> tuple[str, str]:
    """Peel leading parameter attributes off an operand's value text."""
    attrs: list[str] = []
    rest = text.strip()
    while True:
        m = re.match(r"^([A-Za-z_]\w*)(\s*\([^)]*\))?\s*(.*)$", rest, re.DOTALL)
        if not m:
            break
        word = m.group(1)
        if word not in _PARAM_ATTRS:
            break
        attrs.append(word + (m.group(2) or "").strip())
        rest = m.group(3).strip()
    return " ".join(attrs), rest


def parse_operand(text: str) -> Operand:
    """Split ``"%Qubit* nonnull inttoptr (i64 1 to %Qubit*)"`` into its parts."""
    m = _TYPE_RE.match(text)
    if not m:
        return Operand(type="", value=text.strip())
    type_text = re.sub(r"\s+", "", m.group("type"))
    attrs, value = _strip_param_attrs(m.group("rest"))
    return Operand(type=type_text, value=value.strip(), attrs=attrs)


# --------------------------------------------------------------------------
# Qubit / Result operands
# --------------------------------------------------------------------------

_INTTOPTR = re.compile(
    r"^inttoptr\s*\(\s*i\d+\s+(?P<id>-?\d+)\s+to\s+%(?P<ty>Qubit|Result)\s*\*\s*\)$"
)
_SSA = re.compile(r"^(%[\w.$-]+|%\"[^\"]*\")$")


def parse_pointer(operand: Operand, alloc_names: set[str] | None = None) -> PointerRef:
    """Classify a ``%Qubit*``/``%Result*`` operand.

    ``alloc_names`` holds the SSA names known to come straight out of
    ``__quantum__rt__qubit_allocate``; those are distinct by construction.
    """
    pointee = "Result" if "Result" in operand.type else "Qubit"
    value = operand.value.strip()
    if value == "null":
        return PointerRef(RefKind.STATIC, pointee, id=0, text=value)
    m = _INTTOPTR.match(re.sub(r"\s+", " ", value))
    if m:
        return PointerRef(
            RefKind.STATIC, m.group("ty"), id=int(m.group("id")), text=value
        )
    if _SSA.match(value):
        kind = (
            RefKind.ALLOC
            if alloc_names and value in alloc_names
            else RefKind.UNKNOWN
        )
        return PointerRef(kind, pointee, name=value, text=value)
    return PointerRef(RefKind.UNKNOWN, pointee, name=value, text=value)
