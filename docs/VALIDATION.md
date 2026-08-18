# Validation methodology

This is the evidence for one specific claim: **every rewrite Qizil makes
preserves the circuit's unitary exactly, up to a global phase that is
tracked and reported, never silently discarded.** It is written for a
reader auditing that claim, not extending the codebase — for the mechanics
of *how* the checker is implemented, see [`design.md`](design.md#8-verification-strategy).
Every number below is reproducible; commands are given throughout.

## The claim, precisely

For an input module `M` and its optimized output `M'`:

```
U(M') = exp(i * phi) * U(M)
```

where `U(·)` is the unitary implemented by the module's quantum instructions
and `phi` is a real number Qizil reports as `global_phase`. This must hold
**exactly** (to floating-point precision, not "approximately" or
"heuristically") for every rewrite the optimizer makes, and Qizil's design
treats a violation of it as the only kind of bug that matters more than any
other: several are documented below, each one caught before shipping, each
one because of the methodology described here — not despite it.

## Layer 1 — the algebra is closed-form, not learned or heuristic

Every rewrite is justified by an identity in `qizil.ir.gates` /
`qizil.passes.algebra`: single-qubit gates are normalized to
`exp(i*phase) * R_axis(angle)`, and folding a run is addition of angles and
phases in that normal form. There is no approximation, tolerance-fitting, or
numerical search anywhere in the rewrite logic itself — `H·H = I`,
`Rz(a)·Rz(b) = Rz(a+b)`, and the Clifford+T ladder in
`qizil.passes.algebra.synthesize_axis` are algebraic facts, checked by
construction. This is why the optimizer can be *fast*: it never needs to
simulate anything to decide whether a rewrite is legal.

## Layer 2 — every rewrite is checked against an independent reference simulator, not just derived

Layer 1 says the rewrites *should* be correct. Layer 2 does not trust that —
it recomputes the unitary of every gate run from the actual gate matrices
(`qizil.verify.matrices`) and compares before against after, numerically,
for both the module as a whole and for every gate-run segment individually.
Structural equivalence is checked first and separately: both modules are
split into segments at every non-unitary instruction (measurement, reset,
runtime call, classical code, branch), and those segments must match
**textually**, not just numerically — this is what stops a rewrite from
hiding a reordered measurement or a changed branch behind a matching matrix.
Two independent implementations exist and are cross-checked against each
other on every optimization run where numpy is present
(`qizil.verify.simulator`, dependency-free; `qizil.verify.accelerated`,
numpy-backed) — see "What is NOT verified" below for the size limits both
carry.

```console
qizil verify before.ll after.ll     # or: qizil optimize --verify
```

## Layer 3 — a closed-form ground truth, not a self-reported one

Everything above establishes that Qizil's output matches Qizil's input, as
computed by Qizil's own reference simulator. That is real evidence, but it
is still Qizil checking Qizil. `examples/qft_roundtrip.ll` is different: it
is a Quantum Fourier Transform immediately followed by its own exact
inverse, decomposed into CNOT/Rz the way a real compiler emits it (QIR has
no native controlled-phase gate). **QFT · QFT⁻¹ = I is a mathematical fact
independent of this tool** — checkable by hand from the `H·H = I`,
`CNOT·CNOT = I`, `Rz(t)·Rz(-t) = I` identities alone, and from the
construction in `examples/gen_qft.py` (forward gate list, reversed and
adjointed for the inverse). Running it through the optimizer:

```console
$ qizil examples/qft_roundtrip.ll -O3 --verify
  gates          94 -> 0      (-100.0%)
  depth          55 -> 1      (-98.2%)
  verify: unitary preserved over 1 segment(s), max error 1.44e-15
```

94 gates collapse to zero (only the 5 measurements remain), the reported
`global_phase` is exactly `0.0` — as it must be, since every gate matrix in
this construction (`H`, `CNOT`, `Rz`) is phase-free by definition in this
project's normal form — and the reference simulator independently confirms
the result is the identity to `1e-15`. This is the strongest single
correctness demonstration in the project specifically because the answer
was known before the tool ran.

## Layer 4 — breadth, not just depth

A handful of examples proves the algebra is *sound*; it does not prove the
*implementation* of the passes is free of the ordinary kind of bug (an
off-by-one, a wrong branch, an edge case in commutation analysis). For that,
the test suite runs the full pipeline against hundreds of **randomly
generated** circuits, spanning every gate in the table, every optimization
level, and both reference-simulator backends, and checks every one against
Layer 2's numerical verification:

| what | where | count |
|---|---|---|
| randomized circuits, numpy backend (when available) | `tests/test_semantics.py` | 218 |
| randomized circuits, pure-Python backend (always) | `tests/test_semantics_pure.py` | 91 |
| shipped examples, every optimization level | both files above | 24 |
| dedicated verifier correctness (see Layer 5) | `tests/test_accelerated.py` | 8 |
| control-flow / fidelity invariants (barriers never crossed, `-O0` is byte-identical, etc.) | `tests/test_control_flow.py`, `tests/test_parser.py` | 33 |

```console
pytest -q                                    # 484 tests total, ~10s
pytest tests/test_semantics.py -q            # the randomized proof, numpy backend
pytest tests/test_semantics_pure.py -q       # the same proof, zero dependencies
```

A CI job (`.github/workflows/ci.yml`) runs this matrix with **no optional
dependencies installed at all**, specifically so the randomized-equivalence
proof cannot silently stop running in an environment without numpy — an
earlier version of this test suite had exactly that gap (a module-level
`pytest.importorskip("numpy")` that hid ~220 tests from collection instead
of skipping them individually; see the git history on
`tests/test_semantics_pure.py` for the full account). The lesson generalizes:
**a correctness claim is only as strong as the environment it was last
actually exercised in**, which is why that gap mattered enough to fix rather
than note and move on from.

## Layer 5 — the checker itself is audited, not assumed correct

The reference simulator is the oracle every other layer trusts; a bug in it
would be invisible to everything above. It is held to the same standard as
the code it's checking:

- The numpy backend's gate-application routine
  (`qizil.verify.accelerated._apply`) is cross-checked against an
  independently-implemented dense embedding, exhaustively, over every gate
  arity used in this project's gate table (1, 2, 3 qubits) and every
  possible target-qubit ordering — not spot-checked (`tests/test_accelerated.py`).
  This exists because a plausible-looking rewrite of that function once
  silently swapped operand order for every multi-qubit gate; the exhaustive
  check caught it before a commit.
- The two backends (`simulator.py`, dependency-free; `accelerated.py`,
  numpy) are cross-checked against each other on random circuits
  (`test_backends_agree`), not just each checked against itself.
- `qizil verify` additionally runs the optimized module through LLVM's own
  IR verifier when PyQIR is installed (`--llvm-check`), an independent,
  external tool checking that the output is well-formed IR at all —
  orthogonal to, and not a substitute for, the unitary-equivalence check.

## What is *not* verified

Stated precisely, because an unstated limitation is worse than a stated one:

- **Qubit count.** Dense unitary comparison is `O(dim²)` per gate at best
  (`dim = 2^qubits`); the pure-Python backend caps at 8 qubits, numpy at 12.
  Beyond that, `verify_equivalence` reports `available=False` — never a
  false `ok=True`. See [`BENCHMARKS.md`](BENCHMARKS.md#2-the-equivalence-verifier)
  for the exact cost curve and why raising the ceiling further is a
  fundamental compute limit, not an implementation gap.
- **Segment size within the qubit limit.** Both backends also decline (same
  `available=False` path) a segment whose (qubit count x gate count) would
  take an unreasonable amount of wall-clock time even within the qubit
  ceiling — a coarse, documented heuristic
  (`qizil.verify.accelerated._MAX_COST_UNITS` and its pure-Python
  equivalent), not a hard correctness boundary.
- **Dynamic qubit operands.** A `%Qubit*` that is not a compile-time
  constant or a direct `qubit_allocate` result cannot be resolved to a
  specific qubit, so the module is reported unverifiable rather than guessed
  at (`Unsupported: module contains dynamic qubit operands`).
- **`ok=True` requires actual evidence.** If every segment in a module gets
  skipped (too many qubits, or too large), the result is `ok=False,
  available=False` — not a default-true with zero segments checked. (A
  module with no quantum content at all is a different, legitimate case:
  zero segments exist to check, and `ok=True` is correct there.) This is a
  fixed bug, not a hypothetical: an earlier version of `verify_equivalence`
  defaulted `ok=True` and only ever set it to `False` on an explicit
  mismatch, so an all-segments-skipped module reported "verified" on the
  strength of nothing — `tests/test_semantics.py::test_verifier_never_claims_ok_with_zero_evidence`
  is the permanent regression guard.
- **Controlled functors (`__ctl`, `__ctladj`) and unrecognized gates** are
  treated as opaque barriers everywhere in the pipeline, including
  verification — never silently passed through as if understood.
- **A time-limited pipeline run** (`time_budget_s`) can leave the module
  less optimized than it could be, but never incorrect: every individual
  rewrite already preserves the unitary on its own, independent of how many
  of them ran. This is a property of the *rewrite* layer, not something the
  verifier layer needs to additionally check.
