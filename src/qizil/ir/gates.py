"""The QIS gate table: names, arities, and the algebra the passes rely on.

Every rewrite in Qizil is justified by an entry in this table, so the table is
written in one canonical form: a single-qubit gate is described as

    U = exp(i * phase) * R_axis(angle),   R_axis(t) = exp(-i * t * P_axis / 2)

which makes cancellation, fusion and Clifford+T resynthesis the same
computation — add up angles and phases, then pick the cheapest sequence that
reproduces the total.  Global phase is tracked explicitly rather than ignored,
so a rewrite is never "approximately" right.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = [
    "GateSpec",
    "AxisGate",
    "GATES",
    "QIS_PREFIX",
    "lookup",
    "split_qis_name",
    "qis_name",
    "axis_gate_of",
    "CORE_SYNTHESIS_GATES",
]

QIS_PREFIX = "__quantum__qis__"
_QIS_RE = re.compile(r"^__quantum__qis__(?P<base>[a-z0-9_]+?)__(?P<functor>body|adj|ctl|ctladj)$")

PI = math.pi


@dataclass(frozen=True)
class AxisGate:
    """``exp(i*phase) * R_axis(angle)`` — the normal form for 1-qubit gates."""

    axis: str
    angle: float
    phase: float = 0.0


@dataclass(frozen=True)
class GateSpec:
    """Static description of one QIS operation."""

    name: str
    num_qubits: int
    num_params: int = 0
    #: Per qubit slot, the Pauli axes this gate acts through.  Two gates
    #: commute on a shared qubit when their slots share an axis.
    axes: tuple[frozenset[str], ...] = ()
    #: ``U @ U == I`` (for the ``body`` functor).
    hermitian: bool = False
    #: Qubit operands may be permuted without changing the unitary.
    symmetric: bool = False
    #: Normal form for fixed single-qubit gates (``body`` functor).
    axis_gate: AxisGate | None = None
    #: Rotation axis for parametric single-qubit gates (rx/ry/rz).
    rot_axis: str | None = None
    #: Rotation axis for parametric two-qubit gates (rxx/ryy/rzz).
    pair_axis: str | None = None
    #: "gate" | "measure" | "reset" | "readout"
    kind: str = "gate"
    #: Number of ``%Result*`` operands.
    num_results: int = 0
    #: Result is returned rather than passed as an out-parameter.
    returns_result: bool = False
    #: LLVM signature used when Qizil has to synthesize a declaration.
    signature: str = ""

    @property
    def is_rotation(self) -> bool:
        return self.rot_axis is not None or self.pair_axis is not None

    def slot_axes(self, i: int) -> frozenset[str]:
        return self.axes[i] if i < len(self.axes) else frozenset()


_ALL = frozenset({"X", "Y", "Z"})
_X, _Y, _Z = frozenset({"X"}), frozenset({"Y"}), frozenset({"Z"})
_NONE: frozenset[str] = frozenset()


def _one(name, *, axes, hermitian=False, axis_gate=None, rot_axis=None, kind="gate"):
    sig = "void (double, %Qubit*)" if rot_axis else "void (%Qubit*)"
    return GateSpec(
        name=name,
        num_qubits=1,
        num_params=1 if rot_axis else 0,
        axes=(axes,),
        hermitian=hermitian,
        axis_gate=axis_gate,
        rot_axis=rot_axis,
        kind=kind,
        signature=sig,
    )


GATES: dict[str, GateSpec] = {
    # ---- fixed single-qubit gates -------------------------------------
    "i": _one("i", axes=_ALL, hermitian=True, axis_gate=AxisGate("Z", 0.0, 0.0)),
    "id": _one("id", axes=_ALL, hermitian=True, axis_gate=AxisGate("Z", 0.0, 0.0)),
    "x": _one("x", axes=_X, hermitian=True, axis_gate=AxisGate("X", PI, PI / 2)),
    "y": _one("y", axes=_Y, hermitian=True, axis_gate=AxisGate("Y", PI, PI / 2)),
    "z": _one("z", axes=_Z, hermitian=True, axis_gate=AxisGate("Z", PI, PI / 2)),
    "s": _one("s", axes=_Z, axis_gate=AxisGate("Z", PI / 2, PI / 4)),
    "t": _one("t", axes=_Z, axis_gate=AxisGate("Z", PI / 4, PI / 8)),
    # H is Hermitian but acts along (X+Z)/sqrt(2); it commutes with nothing.
    "h": _one("h", axes=_NONE, hermitian=True),
    # ---- parametric single-qubit rotations ----------------------------
    "rx": _one("rx", axes=_X, rot_axis="X"),
    "ry": _one("ry", axes=_Y, rot_axis="Y"),
    "rz": _one("rz", axes=_Z, rot_axis="Z"),
    # ---- multi-qubit ---------------------------------------------------
    "cnot": GateSpec(
        "cnot", 2, axes=(_Z, _X), hermitian=True, signature="void (%Qubit*, %Qubit*)"
    ),
    "cx": GateSpec(
        "cx", 2, axes=(_Z, _X), hermitian=True, signature="void (%Qubit*, %Qubit*)"
    ),
    "cy": GateSpec(
        "cy", 2, axes=(_Z, _Y), hermitian=True, signature="void (%Qubit*, %Qubit*)"
    ),
    "cz": GateSpec(
        "cz",
        2,
        axes=(_Z, _Z),
        hermitian=True,
        symmetric=True,
        signature="void (%Qubit*, %Qubit*)",
    ),
    "swap": GateSpec(
        "swap",
        2,
        axes=(_NONE, _NONE),
        hermitian=True,
        symmetric=True,
        signature="void (%Qubit*, %Qubit*)",
    ),
    "ccx": GateSpec(
        "ccx",
        3,
        axes=(_Z, _Z, _X),
        hermitian=True,
        signature="void (%Qubit*, %Qubit*, %Qubit*)",
    ),
    "rxx": GateSpec(
        "rxx",
        2,
        1,
        axes=(_X, _X),
        symmetric=True,
        pair_axis="X",
        signature="void (double, %Qubit*, %Qubit*)",
    ),
    "ryy": GateSpec(
        "ryy",
        2,
        1,
        axes=(_Y, _Y),
        symmetric=True,
        pair_axis="Y",
        signature="void (double, %Qubit*, %Qubit*)",
    ),
    "rzz": GateSpec(
        "rzz",
        2,
        1,
        axes=(_Z, _Z),
        symmetric=True,
        pair_axis="Z",
        signature="void (double, %Qubit*, %Qubit*)",
    ),
    # ---- measurement / reset -------------------------------------------
    "mz": GateSpec(
        "mz",
        1,
        axes=(_NONE,),
        kind="measure",
        num_results=1,
        signature="void (%Qubit*, %Result*)",
    ),
    "m": GateSpec(
        "m",
        1,
        axes=(_NONE,),
        kind="measure",
        num_results=1,
        returns_result=True,
        signature="%Result* (%Qubit*)",
    ),
    "measure": GateSpec(
        "measure",
        1,
        axes=(_NONE,),
        kind="measure",
        num_results=1,
        signature="void (%Qubit*, %Result*)",
    ),
    "reset": GateSpec("reset", 1, axes=(_NONE,), kind="reset", signature="void (%Qubit*)"),
    "read_result": GateSpec(
        "read_result",
        0,
        kind="readout",
        num_results=1,
        signature="i1 (%Result*)",
    ),
}

#: Gates Qizil is willing to synthesize even when the input module never used
#: them.  They are the universally-supported core of the QIR ``qis`` set.
CORE_SYNTHESIS_GATES = frozenset({"x", "y", "z", "h", "s", "t", "rx", "ry", "rz"})


def split_qis_name(func: str) -> tuple[str, str] | None:
    """``__quantum__qis__rz__body`` -> ``("rz", "body")``."""
    m = _QIS_RE.match(func)
    if not m:
        return None
    return m.group("base"), m.group("functor")


def qis_name(base: str, functor: str = "body") -> str:
    return f"{QIS_PREFIX}{base}__{functor}"


def lookup(base: str) -> GateSpec | None:
    return GATES.get(base)


def axis_gate_of(spec: GateSpec, functor: str, angle: float | None) -> AxisGate | None:
    """Normal form of a single-qubit gate instance, or ``None``.

    ``functor`` is ``"body"`` or ``"adj"``; the adjoint negates both the
    rotation angle and the global phase.
    """
    if spec.rot_axis is not None:
        if angle is None:
            return None
        base = AxisGate(spec.rot_axis, angle, 0.0)
    elif spec.axis_gate is not None:
        base = spec.axis_gate
    else:
        return None
    if functor == "adj":
        return AxisGate(base.axis, -base.angle, -base.phase)
    if functor != "body":
        return None
    return base
