# Extending Qizil

## Adding a gate

Gates live in one table: `qizil.ir.gates.GATES`. Adding an entry is the only
step needed for the parser to recognize it, the DAG to order it, the metrics to
count it and the passes to reason about it.

```python
"cs": GateSpec(
    "cs", 2,                       # name, qubit count
    axes=(_Z, _Z),                 # Pauli axis per operand slot
    symmetric=True,                # operands may be swapped
    signature="void (%Qubit*, %Qubit*)",
),
```

The fields that matter:

| field | meaning | get it wrong and… |
| --- | --- | --- |
| `axes` | Pauli axis each operand slot acts through; empty set = "commutes with nothing on that qubit" | gates get reordered when they must not — **always err towards the empty set** |
| `hermitian` | `U·U == I` | pairs cancel that should not |
| `symmetric` | operand order is irrelevant | `CX(a,b)` would cancel `CX(b,a)` |
| `axis_gate` | `AxisGate(axis, angle, phase)` normal form for a fixed 1-qubit gate | folding produces the wrong unitary or phase |
| `rot_axis` / `pair_axis` | parametric rotation axis | rotations fuse across different axes |
| `signature` | LLVM type, used when Qizil has to emit a `declare` | invalid output IR |

`axis_gate` must satisfy `U = exp(i·phase)·R_axis(angle)` exactly, phase
included. Check it against `qizil.verify.unitary.gate_matrix` — and add the
matrix there too, otherwise `--verify` will report the gate as unsupported and
skip the segment rather than checking it.

A gate that is only in `GATES` but not in `CORE_SYNTHESIS_GATES` is never
introduced into a module that did not already use it (see
`SynthesisPolicy.can_emit`). Keep that list to gates every backend supports.

Finally, add cases to `tests/test_analysis.py::test_commutation_rules` and let
the randomized suite in `tests/test_semantics.py` cover the algebra: put the
new gate in `conftest.random_gates` and the reference simulator will check
every rewrite involving it.

## Adding a pass

Two shapes are available.

**Pairwise rewrites** — "these two gates can become something cheaper" —
subclass `PairPass` and implement `combine`:

```python
from qizil.passes.rewrite import PairPass, Rewrite, same_qubits

class HZHPass(PairPass):
    name = "hzh"
    description = "H Z H -> X"
    through_commuting = False          # True to search past commuting gates

    def combine(self, a, b, ctx):
        if not same_qubits(a, b):
            return None
        ...
        return Rewrite(gates=(("x", "body", None),), residual_phase=0.0)
```

`PairPass` handles the search (`forward_candidates` walks forward, stopping at
the first blocking instruction), the splice, the declaration of any new gate,
the phase bookkeeping and the statistics. `combine` only has to answer the
algebra question. The rewrite is placed at the position of the *later* gate,
which is always legal: gates define no SSA values, so moving one later can
never break dominance.

**Run rewrites** — "this whole sequence collapses" — subclass `Pass` and work
on `block.instructions` directly; `CliffordTPass` is the worked example. If you
gather a run across intervening instructions, gate each hop on
`commutes_with_axis` (or `commutes`) and never hop a barrier.

Register the pass in `qizil.passes.manager.PASS_REGISTRY` and add it to the
`PIPELINES` levels it belongs in.

### Rules a pass must not break

- Only rewrite when every qubit involved is `is_definite()`.
- Never move an instruction across a `BARRIER` or `TERMINATOR`.
- Never move an instruction into a different basic block.
- Report every phase you drop as `residual_phase`; do not round it away.
- Respect `ctx.policy` — `can_emit` for the gate set, `preserve_global_phase`
  for phase-inexact rewrites.
- Make the rewrite a fixed point: applying the pass to its own output must
  produce no further changes, or `PassManager` will spin until its iteration
  cap. `tests/test_semantics.py::test_optimization_is_idempotent` checks this.

## Running the tests

```console
pip install -e '.[dev]'
pytest                      # 369 tests, a few seconds
pytest tests/test_semantics.py -q     # randomized equivalence only
```

The randomized suite is the one that matters. If you add an identity to the
gate table or a rewrite to a pass, widen `conftest.random_gates` so the
simulator exercises it, and turn the seed count up locally before shipping:

```python
SEEDS = list(range(400))    # in tests/test_semantics.py
```
