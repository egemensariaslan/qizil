# Design notes

Why Qizil is built the way it is, and what makes each rewrite safe.

## 1. Text in, text out

Qizil parses QIR line by line and keeps every instruction's original text.
Only instructions a pass actually rewrites are re-rendered; everything else is
emitted byte for byte. `-O0` is therefore an exact passthrough, and a diff of
an optimized module shows only the gates that changed.

This is deliberate. A full LLVM round-trip (parse to an in-memory IR, print it
back) reformats constants, reorders attributes, renumbers unnamed values and
drops nothing but changes plenty — noise that makes it hard to trust a quantum
optimizer's output. Line fidelity also means Qizil never needs to understand an
LLVM construct in order to preserve it; anything unrecognized is simply a
barrier it will not move code across.

The trade is that Qizil cannot do anything requiring real IR analysis
(inlining, constant propagation into gate angles, loop transformations). That
is LLVM's job, and Qizil is designed to run alongside it, not replace it.

## 2. What is a qubit, exactly

Every rewrite depends on two questions: *are these the same qubit* (fuse) and
*could these be the same qubit* (order). `qizil.ir.values.PointerRef` answers
both, and classifies each `%Qubit*` operand into one of three kinds:

| kind | comes from | `same_as` | `may_alias` |
| --- | --- | --- | --- |
| `STATIC` | `null`, `inttoptr (i64 N to %Qubit*)` | same `N` | same `N`, or any `ALLOC` |
| `ALLOC` | direct result of `__quantum__rt__qubit_allocate` | same SSA name | same name, or any `STATIC` |
| `UNKNOWN` | function arguments, loads, GEPs, bitcasts | never | **everything** |

`UNKNOWN` aliasing everything is what makes an opaque qubit pointer freeze the
block around it: the DAG links it to every prior instruction and nothing may be
reordered past it.

`STATIC` and `ALLOC` are reported as possibly aliasing even though a
well-formed module never mixes the two addressing schemes. It costs nothing in
practice and removes a whole class of "what if the frontend did something
strange" reasoning.

A released-then-reallocated qubit could make two different `ALLOC` names refer
to the same physical qubit. That is safe here because `__quantum__rt__*` calls
are barriers, so no gate can cross the release that would be needed to expose
the aliasing.

## 3. Barriers

An instruction is a **barrier** — nothing moves across it — when it is:

- a call to any function that is not a recognized `__quantum__qis__*` gate
  (this includes all `__quantum__rt__*` runtime calls);
- a recognized gate with a `__ctl` / `__ctladj` functor, whose control operand
  is an `%Array*` Qizil does not model;
- a gate whose operand types do not match the gate table;
- any non-call instruction that mentions `%Qubit` or `%Result`.

Purely classical instructions (arithmetic, comparisons, `phi`, `load`/`store`
on non-quantum types) are *not* barriers: they cannot observe quantum state, so
gates may be swapped past them. They are never moved themselves.

Basic block terminators end the region. All rewrites are intra-block, which is
what keeps branching and measurement feedback intact by construction.

## 4. The DAG

`qizil.ir.dag.BlockDag` builds, per basic block, a graph over quantum
instructions where an edge means "must stay in this order". Construction is a
single pass with two pieces of state:

- `last_touch[key]` — the last node that touched each definite qubit/result;
- `last_universal` — the last barrier (or any node with a non-definite operand).

A new node takes an edge from `last_touch[k]` for each of its operands, falling
back to `last_universal` when it has not seen that qubit since the last
barrier. A barrier takes an edge from everything and resets the map.

`depth()` on this graph is the circuit depth; `to_dot()` renders it for
`qizil dag --dot`.

## 5. Commutation

Two operations commute if, **on every qubit they share**, both act through the
same Pauli axis. Each gate declares one axis set per operand slot:

```
CX(c, t)   ->  ({Z}, {X})      because CX = |0><0|(x)I + |1><1|(x)X
CZ(a, b)   ->  ({Z}, {Z})
Rz, S, T, Z ->  ({Z},)
Rx, X      ->  ({X},)
H, SWAP    ->  (∅, ...)        acts along more than one axis
I          ->  ({X,Y,Z},)      commutes with everything
```

Sketch of why the rule is sound: if `A = exp(i·H_A)` and `H_A` restricted to
every shared qubit involves only `P_axis` on that qubit, and the same holds for
`B` with the same axis, then `[H_A, H_B] = 0` — the factors on shared qubits
commute because they are the same Pauli, and on unshared qubits the two act on
disjoint subsystems. Hence `[A, B] = 0`.

The rule is conservative, never optimistic: an unmodelled gate has an empty
axis set and commutes with nothing it touches. One extra case is allowed —
identical calls on identical operands always commute, since every operator
commutes with itself.

Measurements, resets and readouts commute only when they share no qubit at all.
Diagonal gates do in fact commute with a Z-measurement, but the gain is small
and the reasoning (branch-local phases) is subtle enough that Qizil declines.

## 6. Normal form and global phase

Every single-qubit gate is written as

```
U = exp(i·phase) · R_axis(angle),      R_axis(t) = exp(-i·t·P_axis/2)
```

so `Z = e^{iπ/2}·Rz(π)`, `S = e^{iπ/4}·Rz(π/2)`, `T = e^{iπ/8}·Rz(π/4)`.
Folding a run is then addition, and the equivalence test is exact:

> `exp(i·p)·R(a)` and `exp(i·p')·R(a')` are the same unitary up to global phase
> **iff** `a ≡ a' (mod 2π)`, and the leftover phase is `p − p' − (a − a')/2`.

That formula is the single source of truth for cancellation, fusion and
Clifford+T resynthesis, and it is why `X·X` cancels exactly (leftover 0) while
`Rz(π) → Z` is reported as carrying a leftover phase of `−π/2`.

The leftovers accumulate in `Module.global_phase`, defined so that

```
U_original == exp(i · module.global_phase) · U_output
```

`--preserve-global-phase` rejects any rewrite with a non-zero leftover. Global
phase is unobservable for a state, but it becomes a *relative* phase if the
block is later lifted into a controlled form, so the option exists.

The verifier measures the same quantity from the matrices, and the test suite
asserts that the measured phase equals the tracked one on every random circuit.

## 7. Resynthesis cost model

`synthesize_axis` generates candidate sequences and ranks them by

```
(number of arbitrary rotations, T-count, gate count, |leftover phase|)
```

Rotations dominate because an arbitrary-angle rotation is not a gate on a
fault-tolerant machine — it is a synthesis problem costing
`0.53·log₂(1/ε) + 5.3` T gates. That ordering is what makes Qizil replace
`Rz(3π/4)` with `S·T`: two gates instead of one, but a T-count of 1 instead of
roughly 50.

## 8. Verification strategy

`qizil.verify.unitary` deliberately does *not* just compare two matrices. Both
modules are split into segments at every non-unitary instruction, and:

1. the fences must match textually, instruction for instruction — this catches
   a reordered measurement, a dropped runtime call or a changed branch;
2. the block and function structure must match — same functions, same block
   count, same labels;
3. each gate run between fences must produce the same matrix up to phase.

Step 1 is the important one. A checker that only compared the overall unitary
of a measurement-free circuit would pass a rewrite that moved a measurement,
which is exactly the failure mode the spec's critical note warns about.
