"""Resource profiler: what the circuit costs on a fault-tolerant machine.

Two backends:

``local`` (default, offline)
    An analytic surface-code model in the style of the Azure Quantum Resource
    Estimator (Beverland et al., *Assessing requirements to scale to practical
    quantum advantage*, arXiv:2211.07629).  The published parts of that model
    are implemented directly — layout overhead, code distance from the
    threshold formula, rotation-synthesis cost, logical cycle time.  The
    T-factory footprint is a **simplified single-species 15-to-1 model**; treat
    it as an order-of-magnitude figure and see :attr:`Estimate.model_notes`.

``azure``
    Submits the QIR to the real ``microsoft.estimator`` target through the
    ``azure-quantum`` package.  Authoritative, needs a workspace and network.

The purpose here is the *delta*: the same model applied before and after
optimization is a fair comparison even where the absolute numbers are
approximate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..ir.module import Module
from .metrics import Metrics, measure

__all__ = [
    "QubitParams",
    "QUBIT_PRESETS",
    "SurfaceCode",
    "Estimate",
    "EstimateComparison",
    "estimate",
    "estimate_azure",
    "compare",
]


@dataclass(frozen=True)
class QubitParams:
    """Physical qubit characteristics (modelled on the Azure RE presets)."""

    name: str
    one_qubit_gate_time_ns: float
    two_qubit_gate_time_ns: float
    measurement_time_ns: float
    error_rate: float
    t_gate_error_rate: float


QUBIT_PRESETS: dict[str, QubitParams] = {
    "qubit_gate_ns_e3": QubitParams("qubit_gate_ns_e3", 50, 50, 100, 1e-3, 1e-3),
    "qubit_gate_ns_e4": QubitParams("qubit_gate_ns_e4", 50, 50, 100, 1e-4, 1e-4),
    "qubit_gate_us_e3": QubitParams("qubit_gate_us_e3", 1e5, 1e5, 1e5, 1e-3, 1e-3),
    "qubit_gate_us_e4": QubitParams("qubit_gate_us_e4", 1e5, 1e5, 1e5, 1e-4, 1e-4),
}


@dataclass(frozen=True)
class SurfaceCode:
    """Surface-code QEC parameters."""

    threshold: float = 0.01
    crossing_prefactor: float = 0.03
    #: physical qubits per logical qubit = factor * d^2
    qubits_per_logical_factor: int = 2
    max_distance: int = 101

    def logical_error_rate(self, physical_error: float, distance: int) -> float:
        return self.crossing_prefactor * (physical_error / self.threshold) ** (
            (distance + 1) / 2
        )

    def distance_for(self, physical_error: float, target: float) -> int:
        for d in range(3, self.max_distance + 1, 2):
            if self.logical_error_rate(physical_error, d) <= target:
                return d
        return self.max_distance


# Rotation synthesis cost: T gates ~= A*log2(1/eps) + B (arXiv:2211.07629 uses
# A = 0.53, B = 5.3 for the mixed fallback protocol).
_SYNTHESIS_A = 0.53
_SYNTHESIS_B = 5.3

# A 15-to-1 distillation round maps input error p to ~35 p^3 and occupies
# roughly 6 logical cycles on ~15 logical qubits' worth of space.
_DISTILL_FANOUT = 15
_DISTILL_ERROR_COEFF = 35.0
_DISTILL_CYCLES = 6


@dataclass
class Estimate:
    backend: str = "local"
    error_budget: float = 1e-3
    qubit_params: str = "qubit_gate_ns_e3"
    algorithmic_qubits: int = 0
    logical_qubits: int = 0
    code_distance: int = 0
    logical_cycle_time_ns: float = 0.0
    logical_depth: int = 0
    t_states: int = 0
    t_states_per_rotation: int = 0
    distillation_rounds: int = 0
    t_factory_copies: int = 0
    physical_qubits_algorithm: int = 0
    physical_qubits_tfactory: int = 0
    physical_qubits: int = 0
    runtime_ns: float = 0.0
    metrics: Metrics | None = None
    model_notes: list[str] = field(default_factory=list)

    @property
    def runtime_seconds(self) -> float:
        return self.runtime_ns * 1e-9

    def to_dict(self) -> dict:
        data = {
            "backend": self.backend,
            "error_budget": self.error_budget,
            "qubit_params": self.qubit_params,
            "algorithmic_qubits": self.algorithmic_qubits,
            "logical_qubits": self.logical_qubits,
            "code_distance": self.code_distance,
            "logical_cycle_time_ns": self.logical_cycle_time_ns,
            "logical_depth": self.logical_depth,
            "t_states": self.t_states,
            "t_states_per_rotation": self.t_states_per_rotation,
            "distillation_rounds": self.distillation_rounds,
            "t_factory_copies": self.t_factory_copies,
            "physical_qubits_algorithm": self.physical_qubits_algorithm,
            "physical_qubits_tfactory": self.physical_qubits_tfactory,
            "physical_qubits": self.physical_qubits,
            "runtime_ns": self.runtime_ns,
            "runtime_seconds": self.runtime_seconds,
            "model_notes": self.model_notes,
        }
        if self.metrics is not None:
            data["metrics"] = self.metrics.to_dict()
        return data


def estimate(
    module: Module,
    error_budget: float = 1e-3,
    qubit_params: str = "qubit_gate_ns_e3",
    qec: SurfaceCode | None = None,
) -> Estimate:
    """Analytic fault-tolerant resource estimate for ``module``."""
    params = QUBIT_PRESETS.get(qubit_params)
    if params is None:
        raise ValueError(
            f"unknown qubit parameter set {qubit_params!r}; "
            f"choose from {', '.join(sorted(QUBIT_PRESETS))}"
        )
    qec = qec or SurfaceCode()
    m = measure(module)

    est = Estimate(
        backend="local",
        error_budget=error_budget,
        qubit_params=qubit_params,
        metrics=m,
    )
    est.model_notes.append(
        "surface-code model after arXiv:2211.07629; T-factory footprint uses a "
        "simplified 15-to-1 model and is indicative only"
    )

    # The budget is split evenly between logical errors, rotation synthesis
    # and T-state distillation, as in the Azure estimator.
    eps_logical = error_budget / 3
    eps_synthesis = error_budget / 3
    eps_distillation = error_budget / 3

    # ---- T states -------------------------------------------------------
    rotations = m.arbitrary_rotations
    if rotations:
        per_rotation_budget = eps_synthesis / rotations
        est.t_states_per_rotation = max(
            1, math.ceil(_SYNTHESIS_A * math.log2(1 / per_rotation_budget) + _SYNTHESIS_B)
        )
    t_states = m.t_gates + m.quarter_rotations + rotations * est.t_states_per_rotation
    # A Toffoli is charged 4 T states.
    t_states += 4 * m.gate_histogram.get("ccx", 0)
    est.t_states = t_states

    # ---- layout ---------------------------------------------------------
    q_alg = max(1, m.qubits)
    est.algorithmic_qubits = q_alg
    est.logical_qubits = 2 * q_alg + math.ceil(math.sqrt(8 * q_alg)) + 1

    # ---- logical depth --------------------------------------------------
    # Each arbitrary rotation expands into a chain of T-state teleportations.
    depth = max(1, m.depth)
    if rotations:
        depth += rotations * (est.t_states_per_rotation - 1)
    est.logical_depth = depth

    # ---- code distance --------------------------------------------------
    # Every logical qubit must survive every logical cycle.
    target_per_operation = eps_logical / max(1, est.logical_qubits * depth)
    est.code_distance = qec.distance_for(params.error_rate, target_per_operation)

    est.logical_cycle_time_ns = (
        4 * params.two_qubit_gate_time_ns + 2 * params.measurement_time_ns
    ) * est.code_distance
    est.runtime_ns = est.logical_cycle_time_ns * depth

    est.physical_qubits_algorithm = (
        est.logical_qubits * qec.qubits_per_logical_factor * est.code_distance**2
    )

    # ---- T factories ----------------------------------------------------
    if t_states:
        target_error = eps_distillation / t_states
        error = params.t_gate_error_rate
        rounds = 0
        while error > target_error and rounds < 4:
            error = _DISTILL_ERROR_COEFF * error**3
            rounds += 1
        est.distillation_rounds = rounds
        if rounds:
            factory_cycles = _DISTILL_CYCLES * rounds
            factory_duration = factory_cycles * est.logical_cycle_time_ns
            # Enough parallel factories to supply all T states within runtime.
            copies = max(
                1, math.ceil(t_states * factory_duration / max(est.runtime_ns, 1e-9))
            )
            est.t_factory_copies = copies
            qubits_per_factory = (
                _DISTILL_FANOUT**rounds
                * qec.qubits_per_logical_factor
                * est.code_distance**2
            )
            est.physical_qubits_tfactory = copies * qubits_per_factory
        else:
            est.model_notes.append(
                "physical T-gate error already meets the distillation budget; "
                "no T factory required"
            )
    est.physical_qubits = est.physical_qubits_algorithm + est.physical_qubits_tfactory
    return est


# --------------------------------------------------------------------------
# Azure Quantum Resource Estimator
# --------------------------------------------------------------------------


def estimate_azure(
    module: Module,
    error_budget: float = 1e-3,
    qubit_params: str = "qubit_gate_ns_e3",
    workspace=None,
    target_id: str = "microsoft.estimator",
    timeout_s: int = 300,
) -> dict:
    """Run the real Azure Quantum Resource Estimator on this module.

    Requires ``pip install 'qizil[azure,bitcode]'`` and an authenticated
    ``azure.quantum.Workspace`` (passed in, or discovered from the environment).
    """
    try:
        from azure.quantum import Workspace
        from azure.quantum.target.microsoft import MicrosoftEstimator
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "the azure backend needs azure-quantum: pip install 'qizil[azure]'"
        ) from exc
    from ..ir.bitcode import to_bitcode

    if workspace is None:  # pragma: no cover - environment dependent
        workspace = Workspace()
    estimator = MicrosoftEstimator(workspace=workspace, name=target_id)
    params = estimator.make_params()
    params.error_budget = error_budget
    params.qubit_params.name = qubit_params
    job = estimator.submit(to_bitcode(module), input_params=params)
    return job.get_results(timeout_secs=timeout_s)


# --------------------------------------------------------------------------
# Before / after
# --------------------------------------------------------------------------


@dataclass
class EstimateComparison:
    before: Estimate
    after: Estimate

    @staticmethod
    def _delta(before: float, after: float) -> dict:
        change = after - before
        pct = (change / before * 100.0) if before else 0.0
        return {"before": before, "after": after, "delta": change, "percent": pct}

    def to_dict(self) -> dict:
        mb, ma = self.before.metrics, self.after.metrics
        out = {
            "physical_qubits": self._delta(
                self.before.physical_qubits, self.after.physical_qubits
            ),
            "runtime_ns": self._delta(self.before.runtime_ns, self.after.runtime_ns),
            "logical_depth": self._delta(
                self.before.logical_depth, self.after.logical_depth
            ),
            "t_states": self._delta(self.before.t_states, self.after.t_states),
            "code_distance": self._delta(
                self.before.code_distance, self.after.code_distance
            ),
        }
        if mb is not None and ma is not None:
            out["gates"] = self._delta(mb.gates, ma.gates)
            out["depth"] = self._delta(mb.depth, ma.depth)
            out["t_gates"] = self._delta(mb.t_gates, ma.t_gates)
            out["arbitrary_rotations"] = self._delta(
                mb.arbitrary_rotations, ma.arbitrary_rotations
            )
        return out


def compare(before: Estimate, after: Estimate) -> EstimateComparison:
    return EstimateComparison(before=before, after=after)
