# Benchmarks

Real numbers, measured on this machine (Apple Silicon, single-threaded
Python 3.12), not estimates. Reproduce any of them with the scripts referenced
inline — none of this is hand-tuned for the writeup.

This document exists because two genuine performance defects were found and
fixed while building this project, and the numbers are worth recording
precisely rather than just claiming "it's fast now." Both are described in
detail in the commit history (`git log --oneline | grep -i fix`); this page
is the durable summary.

## 1. The optimization pipeline

**What it was:** `CliffordTPass` restarted its scan from the top of the
block after every single successful rewrite. For a block with `R` rewrites
to make, that is `O(R)` full rescans, each itself doing `O(n)` work to find
the next candidate — cubic-ish in practice for long blocks.

**The fix:** a single left-to-right sweep, resuming immediately after each
fold instead of restarting (`src/qizil/passes/cliffordt.py`). Correct because
`_gather` already returns the *maximal* run reachable from its starting
index — folding it can only change instructions at or after that index,
never before, so nothing earlier can become newly foldable.

| gates | before | after | speedup |
|---|---|---|---|
| 200 | 38 ms | 18 ms | 2.1x |
| 1,000 | 495 ms | 107 ms | 4.6x |
| 5,000 | 15,273 ms | 707 ms | **21.6x** |
| 20,000 | (not measured — minutes) | 3,823 ms | — |

Current scaling, random circuits, `-O3`, 12 qubits (`tests/test_performance.py`
holds the regression guards for this):

| gates | time | ratio vs. 2x gates |
|---|---|---|
| 500 | 59 ms | — |
| 1,000 | 122 ms | 2.07x |
| 2,000 | 249 ms | 2.04x |
| 4,000 | 497 ms | 2.00x |
| 8,000 | 1,006 ms | 2.02x |

Essentially linear in the number of gates, across a 16x range of input size.

### The known exception: `commute` at very many qubits

`PairPass`'s `through_commuting` search (used by the `commute` pass) walks
forward from a candidate gate past every gate that commutes with it, with no
other stopping condition. On a circuit with **many qubits and low gate
density per qubit** — most gate pairs share no qubit at all, so the walk
rarely terminates early — this is `O(n)` per candidate, `O(n²)` overall.
Measured under conditions constructed to maximize it:

| gates | qubits | time | ratio vs. 2x gates |
|---|---|---|---|
| 1,000 | 50 | 204 ms | — |
| 2,000 | 100 | 365 ms | 1.79x |
| 4,000 | 200 | 1,990 ms | 5.44x |
| 8,000 | 400 | 8,255 ms | 4.15x |

This is a genuine algorithmic characteristic, not a bug: fixing it properly
requires a per-qubit "next relevant instruction" index (the way
`qizil.ir.dag.BlockDag` already achieves `O(n)` construction) threaded through
three passes (`cancel`, `merge-rotations`, `commute`) that are central to the
tool's correctness guarantees. A rewrite under time pressure, without the
same exhaustive validation the verifier rewrite below got, was judged too
risky. Two things bound the practical impact instead:

1. **The regime is unusual for real circuits.** Real algorithmic circuits —
   including every example shipped with this project — have frequent
   multi-qubit gates, which is exactly what keeps `commute`'s forward search
   short in practice (see the near-linear table above, which uses ordinary
   random circuits, not this adversarial construction).
2. **A time budget bounds the worst case.** `time_budget_s` (see below)
   caps how long the pipeline runs regardless: a truncated run is always
   still fully correct, only less optimized.

## 2. The equivalence verifier

**What it was:** the numpy-accelerated backend (`qizil.verify.accelerated`)
applied each gate by embedding it into a full `dim x dim` matrix and
multiplying that into the running product — `total = embed(gate) @ total`.
That is a dense matrix-matrix product per gate, `O(dim³)`. At 12 qubits
(`dim = 4096`) a *single* such multiply measured **4,878 ms**.

**The fix:** reshape the unitary under construction into an `n`-qubit tensor
and contract each gate against only the axes it acts on via
`numpy.tensordot` — the standard technique for local-operator application in
a larger simulation. Real cost `O(dim²)` element-touches per gate,
regardless of arity. Two more attempts at this were tried and rejected
before landing on the tensordot version — one had the right complexity but
was empirically much slower due to numpy fancy-indexing's copy semantics,
one silently computed the wrong answer for multi-qubit gates because of a
bit-ordering assumption that looked obviously correct and wasn't (caught by
an exhaustive cross-check, not by inspection — see
`tests/test_accelerated.py` and the docstring in `accelerated.py` for the
full account).

Per-gate cost, single-qubit gate:

| qubits | dim | before (dense matmul) | after (tensordot) | speedup |
|---|---|---|---|---|
| 8 | 256 | 0.88 ms | 0.18 ms | 4.9x |
| 10 | 1,024 | 76.95 ms | 3.82 ms | 20.1x |
| 12 | 4,096 | 4,878.00 ms | 68.22 ms | **71.5x** |

Realistic verification workloads, end to end (`optimize(..., verify=True)`):

| qubits | gates | time |
|---|---|---|
| 8 | 100 | 68 ms |
| 10 | 100 | 601 ms |
| 10 | 300 | 1,782 ms |
| 12 | 50 | 5,085 ms |

The remaining cost at 12 qubits is not an implementation gap — it is the
`O(dim²)` floor itself. Building a dense unitary for a 12-qubit, thousand-gate
segment is on the order of `10¹⁴` floating-point operations no matter how
it's coded; that regime is declined gracefully (`available=False`, with a
clear message) by a coarse cost guard in both backends rather than left to
hang. See `qizil.verify.accelerated._MAX_COST_UNITS` and the equivalent in
`qizil.verify.simulator`.

## 3. The pipeline time budget

`optimize(..., time_budget_s=N)` stops the optimization pipeline early past
`N` seconds instead of running it to a fixed point. This bounds wall-clock
time on adversarial input without needing to fix the `commute` pass's
worst-case complexity: **every individual rewrite already preserves the
unitary on its own**, so a truncated run can only ever be less optimized,
never incorrect. `result.pipeline` reports exactly where it stopped.

Checked at three levels — between whole pipeline iterations, inside each
pass's own instruction-scanning loop, and inside the innermost candidate
search — all at a 32-step granularity, so a single expensive call can't
blow through the budget either.

| pipeline budget | actual wall-clock (adversarial: 8,000 gates / 400 qubits) |
|---|---|
| 0.2 s | 0.55 s |
| 0.5 s | 0.92 s |
| 1.0 s | 1.42 s |

The residual overshoot is parsing time (outside the budgeted region — see
`api.optimize`'s `time_budget_s` docstring for the exact scope) plus the
last already-in-flight scan step, not an error in the budget mechanism
itself; `tests/test_performance.py::test_time_budget_bounds_wall_clock_on_an_adversarial_circuit`
holds this to a generous 5-second ceiling as a regression guard.

The UI server and `qizil report` default to a 25-second pipeline budget
(`qizil.ui.payload.DEFAULT_TIME_BUDGET_S`) — the actual untrusted-input
surface. The CLI defaults to unlimited (`--time-budget` to override); a
terminal user already has full control and Ctrl-C.

## Reproducing these numbers

Every table above was generated with the same random-circuit helper the test
suite uses (`tests/conftest.py`: `make_ir` + `random_gates`), from a checkout:

```console
pip install -e '.[dev]'
python -c "
import random, sys, time
sys.path.insert(0, 'src'); sys.path.insert(0, 'tests')
from conftest import make_ir, random_gates
from qizil.api import optimize

rng = random.Random(99)
for n in [500, 1000, 2000, 4000, 8000]:
    ir = make_ir(random_gates(rng, n, 12), 12)
    t0 = time.perf_counter()
    optimize(ir, level=3)
    print(n, f'{(time.perf_counter() - t0) * 1000:.0f} ms')
"
```

Or run the performance test files directly with `-v` to see their own
timings, and as a standing regression guard against any of this drifting back:

```console
pytest tests/test_performance.py tests/test_accelerated.py -v
```
