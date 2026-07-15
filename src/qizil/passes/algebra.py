"""Gate algebra: folding runs of gates and resynthesizing the cheapest form.

Everything here works in one normal form (see :mod:`qizil.ir.gates`)::

    U = exp(i * phase) * R_axis(angle)

Folding a run is therefore addition, and equivalence has an exact test: two
normal forms describe the same unitary up to global phase iff their angles
agree modulo 2*pi.  The leftover phase is reported rather than discarded, so
``--preserve-global-phase`` can veto any rewrite that is only equal up to phase
(which matters if the rewritten gates are ever lifted into a controlled form).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..ir.gates import CORE_SYNTHESIS_GATES, AxisGate, GateSpec, axis_gate_of
from ..ir.module import Module, QuantumOp

__all__ = [
    "TOL",
    "SynthesisPolicy",
    "Emission",
    "axis_form",
    "pair_form",
    "fold",
    "synthesize_axis",
    "is_identity",
    "normalize_angle",
    "ROT_NAME",
]

PI = math.pi
TAU = 2 * PI
TOL = 1e-9

ROT_NAME = {"X": "rx", "Y": "ry", "Z": "rz"}
FIXED_NAME = {"X": "x", "Y": "y", "Z": "z"}

#: Clifford+T sequences for Z rotations by k*pi/4, with the (angle, phase) each
#: sequence realizes.  Index = k mod 8.  T-count is 1 for odd k, 0 otherwise.
_Z_LADDER: list[tuple[tuple[tuple[str, str], ...], float, float]] = [
    ((), 0.0, 0.0),
    ((("t", "body"),), PI / 4, PI / 8),
    ((("s", "body"),), PI / 2, PI / 4),
    ((("s", "body"), ("t", "body")), 3 * PI / 4, 3 * PI / 8),
    ((("z", "body"),), PI, PI / 2),
    ((("z", "body"), ("t", "body")), 5 * PI / 4, 5 * PI / 8),
    ((("s", "adj"),), -PI / 2, -PI / 4),
    ((("t", "adj"),), -PI / 4, -PI / 8),
]


def normalize_angle(angle: float, period: float = TAU) -> float:
    """Fold ``angle`` into ``(-period/2, period/2]``."""
    half = period / 2
    value = math.fmod(angle, period)
    if value <= -half:
        value += period
    elif value > half:
        value -= period
    return value


@dataclass
class SynthesisPolicy:
    """Which gates Qizil may write into the output module."""

    available: frozenset[tuple[str, str]]
    mode: str = "auto"  # "auto" | "strict"
    preserve_global_phase: bool = False
    tol: float = TOL

    @classmethod
    def from_module(
        cls,
        module: Module,
        mode: str = "auto",
        preserve_global_phase: bool = False,
        tol: float = TOL,
    ) -> SynthesisPolicy:
        available: set[tuple[str, str]] = set()
        for name in module.declared_gates():
            from ..ir.gates import split_qis_name

            split = split_qis_name(name)
            if split:
                available.add(split)
        return cls(
            available=frozenset(available),
            mode=mode,
            preserve_global_phase=preserve_global_phase,
            tol=tol,
        )

    def can_emit(self, base: str, functor: str = "body") -> bool:
        if (base, functor) in self.available:
            return True
        if self.mode == "strict":
            return False
        # In auto mode the universally supported core set is always available,
        # as is any functor of a gate the module already uses.
        if base in CORE_SYNTHESIS_GATES:
            return True
        return any(b == base for b, _ in self.available)


@dataclass
class Emission:
    """A resynthesized gate run."""

    gates: tuple[tuple[str, str, float | None], ...]  # (base, functor, angle)
    residual_phase: float

    @property
    def t_count(self) -> int:
        return sum(1 for base, _, _ in self.gates if base == "t")

    @property
    def rotation_count(self) -> int:
        """Arbitrary-angle rotations left behind.

        On a fault-tolerant backend each of these has to go through rotation
        synthesis (tens of T gates), so removing one is worth far more than
        removing a single T — hence it dominates the cost ordering.
        """
        return sum(1 for base, _, _ in self.gates if base in ROT_NAME.values())

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.gates)


# --------------------------------------------------------------------------
# Normal forms
# --------------------------------------------------------------------------


def axis_form(op: QuantumOp) -> AxisGate | None:
    """Normal form of a single-qubit gate instance, if it has one."""
    spec: GateSpec = op.spec
    if spec.kind != "gate" or spec.num_qubits != 1:
        return None
    angle: float | None = None
    if spec.num_params:
        if not op.angles or op.angles[0] is None:
            return None  # symbolic angle: not foldable
        angle = op.angles[0]
    return axis_gate_of(spec, op.functor, angle)


def pair_form(op: QuantumOp) -> AxisGate | None:
    """Normal form of a two-qubit Pauli rotation (rxx / ryy / rzz)."""
    spec = op.spec
    if spec.pair_axis is None or not op.angles or op.angles[0] is None:
        return None
    angle = op.angles[0]
    if op.functor == "adj":
        angle = -angle
    elif op.functor != "body":
        return None
    return AxisGate(spec.pair_axis, angle, 0.0)


def fold(forms: list[AxisGate]) -> tuple[float, float]:
    """Sum a run of same-axis normal forms into ``(angle, phase)``."""
    return (
        math.fsum(f.angle for f in forms),
        math.fsum(f.phase for f in forms),
    )


def is_identity(angle: float, phase: float, tol: float = TOL) -> tuple[bool, float]:
    """Is ``exp(i*phase) R(angle)`` the identity up to global phase?

    Returns ``(yes, residual_phase)``.
    """
    if abs(normalize_angle(angle)) > tol:
        return False, 0.0
    residual = phase - angle / 2.0
    return True, normalize_angle(residual)


# --------------------------------------------------------------------------
# Resynthesis
# --------------------------------------------------------------------------


def _residual(angle: float, phase: float, out_angle: float, out_phase: float) -> float:
    """Global phase left over after replacing R(angle)e^{i.phase} by the output.

    Equal-up-to-phase requires ``angle == out_angle (mod 2*pi)``; the leftover
    phase then follows from the eigenvalue bookkeeping of R.
    """
    return normalize_angle(phase - out_phase - (angle - out_angle) / 2.0)


def synthesize_axis(
    axis: str,
    angle: float,
    phase: float,
    policy: SynthesisPolicy,
    *,
    allow_fixed: bool = True,
) -> Emission | None:
    """Cheapest gate sequence equal to ``exp(i*phase) R_axis(angle)``.

    Returns ``None`` when no legal sequence exists under ``policy`` (missing
    gates, or a phase change that ``preserve_global_phase`` forbids).
    """
    tol = policy.tol
    reduced = angle % TAU
    k = round(reduced / (PI / 4))
    quarter_exact = abs(reduced - k * (PI / 4)) < tol
    k %= 8

    candidates: list[Emission] = []

    if quarter_exact and allow_fixed:
        if axis == "Z":
            seq, out_angle, out_phase = _Z_LADDER[k]
            if all(policy.can_emit(b, f) for b, f in seq):
                candidates.append(
                    Emission(
                        tuple((b, f, None) for b, f in seq),
                        _residual(angle, phase, out_angle, out_phase),
                    )
                )
        else:
            if k == 0:
                candidates.append(Emission((), _residual(angle, phase, 0.0, 0.0)))
            elif k == 4 and policy.can_emit(FIXED_NAME[axis]):
                candidates.append(
                    Emission(
                        ((FIXED_NAME[axis], "body", None),),
                        _residual(angle, phase, PI, PI / 2),
                    )
                )
    elif quarter_exact and k == 0:
        candidates.append(Emission((), _residual(angle, phase, 0.0, 0.0)))

    rot = ROT_NAME[axis]
    if policy.can_emit(rot):
        # Prefer the angle that leaves no phase behind, then the small one.
        for out_angle in (angle, normalize_angle(angle)):
            candidates.append(
                Emission(
                    ((rot, "body", out_angle),),
                    _residual(angle, phase, out_angle, 0.0),
                )
            )

    legal = [
        c
        for c in candidates
        if not (policy.preserve_global_phase and abs(c.residual_phase) > tol)
    ]
    if not legal:
        return None
    legal.sort(
        key=lambda c: (c.rotation_count, c.t_count, len(c.gates), abs(c.residual_phase))
    )
    return legal[0]
