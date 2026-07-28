# Qizil — QIR-Opt

A compiler optimization module for **QIR** (Quantum Intermediate Representation).
Qizil reads QIR as `.ll` or `.bc`, builds a quantum instruction DAG, applies
peephole cancellation, rotation fusion, commutation-based reordering and
Clifford+T resynthesis, and writes back optimized QIR — with every classical
instruction, basic block, measurement and piece of metadata exactly where it was.

## Quick start

Two commands. No install, no dependencies, no virtualenv — any Python ≥ 3.10:

```console
git clone https://github.com/egemen/qizil && cd qizil

./qizil examples/trotter_step.ll -O3 -o optimized.ll
```

```
qizil 0.1.0  examples/trotter_step.ll  (-O3)

  metric                   before      after   change
  ----------------------------------------------------
  qubits                        4          4       0%
  quantum instructions        104         76   -26.9%
  gates                       100         72   -28.0%
  1-qubit gates                76         50   -34.2%
  2-qubit gates                24         22    -8.3%
  arbitrary rotations          44         34   -22.7%
  depth                        53         44   -17.0%
  measurements                  4          4       0%

  passes: cancel x9, merge-rotations x9, commute x1  (2 iterations)
  gates:  cnot 24->22, h 32->16, mz 4, rz 44->34
```

That is the whole setup. `./qizil` works from any directory
(`/path/to/qizil/qizil input.ll -O2 -o out.ll`), and on Windows as
`python qizil input.ll -O2 -o out.ll`.

Add `--verify` to have the rewrite *proved* against a reference simulator
(the only flag that wants a dependency — without numpy it reports `skipped`,
never a false pass):

```console
pip install numpy
./qizil examples/trotter_step.ll -O3 -o optimized.ll --verify
#   verify: unitary preserved over 1 segment(s), max error 2.27e-15
```

See it, rather than read it — a browser UI with circuit diagrams, before/after
charts, the rewrite trace and the equivalence proof:

```console
./qizil ui                                   # opens http://127.0.0.1:8731
./qizil report input.ll -O3 -o report.html   # same page as one shareable file
```

**Zero runtime dependencies.** The parser, the DAG, the passes, the metrics,
the equivalence checker and the UI are all pure Python stdlib — no numpy, no
web framework, no CDN. PyQIR is optional and only used at the edges (bitcode
in/out, LLVM verification); numpy is optional and only makes the equivalence
check faster and wider.

---

## Why

QIR is the industry standard for representing quantum programs on top of LLVM,
but native optimization passes for it are sparse. QIR emitted by Q#, Qiskit or
PennyLane routinely contains redundant unitaries, uncoalesced rotations and
avoidable `T` gates. On a fault-tolerant machine those are not cosmetic: a `T`
gate needs a magic state factory, and an *arbitrary-angle* rotation needs tens
of `T` gates of synthesis. Cutting them cuts physical qubits and wall-clock
runtime.

## Install (optional)

Nothing here is required — `./qizil` in a clone is fully functional. Install
only if you want `qizil` on your PATH without the clone path:

```console
uv tool install '.[verify,bitcode]'     # or: pipx install '.[verify,bitcode]'
pip install -e '.[dev]'                 # or into an active virtualenv, + pytest
```

The extras are all optional; the core never needs them:

| extra | adds | needs |
| --- | --- | --- |
| *(none)* | parsing, all four passes, metrics, estimator | — |
| `bitcode` | `.bc` input/output, `--llvm-check` | PyQIR |
| `verify` | faster checking, up to 12 qubits (the check itself needs nothing) | numpy |
| `azure` | the real Azure Quantum Resource Estimator backend | azure-quantum |

> Not on PyPI yet. Once published, `pip install qizil` (or `uvx qizil …` to run
> it without installing) replaces the clone step above.

## The UI

`./qizil ui` serves a local page (stdlib `http.server`, loopback only) that
runs the pipeline live: pick a circuit, slide between `-O0` and `-O3`, and
watch what each level does.

| panel | what it shows |
| --- | --- |
| proof banner | equivalence verdict, max matrix error, segments checked, simulator used, global phase, LLVM verdict |
| circuit | the actual circuit before and after — qubit wires, CNOT controls, rotation angles, T gates highlighted; hover a gate for its QIR instruction |
| metrics | before/after bars for instructions, gates, depth, T-count, arbitrary rotations |
| fault-tolerant resources | logical qubits, code distance, T states, physical qubits, runtime |
| rewrite trace | every rewrite with the identity that justifies it (`h(q0) · h(q0) = I`) |
| gates by kind | histogram of every operation |
| QIR diff | the changed lines, with everything else emitted byte for byte |

`./qizil report input.ll -o report.html` writes the same page as a single
self-contained file — CSS, JS and data inlined, no network access — for a
paper, a PR comment, or a CI artifact.

## Command line

Everything below works as `./qizil ...` from a clone, or as `qizil ...` once
installed.

```console
qizil input.ll -O2 -o output.ll        # optimize (the subcommand is optional)
qizil input.bc -O3 -o output.bc        # bitcode in, bitcode out
qizil stats input.ll                   # gate counts, depth, T-count
qizil estimate input.ll -O2            # fault-tolerant resources, before vs after
qizil verify before.ll after.ll        # prove two modules are the same unitary
qizil ui                               # browser UI: diagrams, charts, proof
qizil report input.ll -o report.html   # standalone HTML report
qizil dag input.ll --dot | dot -Tsvg   # visualize the instruction graph
qizil passes                           # list passes and pipelines
```

Useful flags on `optimize`:

| flag | effect |
| --- | --- |
| `-O0 … -O3` | pipeline selection; `-O0` is a byte-exact passthrough |
| `--passes cancel,commute` | run exactly these passes |
| `--disable clifford-t` | drop a pass from the pipeline |
| `--gateset strict` | never introduce a gate the input did not already use |
| `--preserve-global-phase` | reject rewrites that are only correct up to phase |
| `--verify` | check unitary equivalence against the input (needs numpy) |
| `--llvm-check` | run the output through LLVM's verifier (needs PyQIR) |
| `--report r.json` | machine-readable report of every rewrite |
| `-v` | print each rewrite as it is applied |

## Python API

```python
import qizil

result = qizil.optimize("circuit.ll", level=2, verify=True)

print(result.before.gates, "->", result.after.gates)
print(result.after.t_count, "T gates remain")
print(result.verification.ok)               # True
open("out.ll", "w").write(result.to_ll())

est = result.estimates(error_budget=1e-3)   # before/after resource estimate
print(est.to_dict()["physical_qubits"])
```

Lower-level pieces are public too:

```python
from qizil import parse_file, build_pipeline, PassManager
from qizil.passes import PassContext, SynthesisPolicy
from qizil.ir.dag import BlockDag

module = parse_file("circuit.ll")
ctx = PassContext(policy=SynthesisPolicy.from_module(module))
PassManager(build_pipeline(level=2), max_iterations=8).run(module, ctx)

for fn, block in module.blocks():
    print(BlockDag(block).to_dot(fn.name))
```

## Passes

| pass | `-O` | what it does |
| --- | --- | --- |
| `cancel` | 1, 2, 3 | deletes adjacent pairs whose product is the identity: `H·H`, `X·X`, `CX·CX`, `T·T†`, `Rz(θ)·Rz(−θ)` |
| `merge-rotations` | 1, 2, 3 | fuses same-axis rotations on the same qubits: `Rz(θ₁)·Rz(θ₂) → Rz(θ₁+θ₂)`, also `Rxx/Ryy/Rzz` |
| `commute` | 2, 3 | re-runs both of the above, searching *through* gates that commute with the candidate |
| `clifford-t` | 2, 3 | collects a maximal same-axis run per qubit and re-emits the cheapest exact sequence |

The pipeline repeats until it reaches a fixed point (`-O2`: up to 8 rounds,
`-O3`: up to 24).

### The commutation rule

Two gates commute when, on **every qubit they share**, both act through the
same Pauli axis — because then their generators commute. Each operand slot of
each gate carries its axis in the gate table:

| gate | slot axes | so it commutes with |
| --- | --- | --- |
| `Rz`, `S`, `T`, `Z` | `Z` | anything diagonal on that qubit |
| `Rx`, `X` | `X` | `X`-type gates on that qubit |
| `CX(c,t)` | `Z` on `c`, `X` on `t` | `Z`-rotations on the control, `X`-rotations on the target |
| `CZ(a,b)` | `Z`, `Z` | diagonals on either qubit |
| `H`, `SWAP` | — | nothing it shares a qubit with |

That is what lets `T · CX(q₀,q₁) · T` become `CX(q₀,q₁) · S`: the CNOT's control
leg is diagonal, so the two `T`s meet.

### Clifford+T resynthesis

Every single-qubit gate is normalized to `exp(i·φ)·R_axis(θ)`, a run is summed,
and the cheapest sequence reproducing the total is emitted:

| total Z rotation | emitted | T-count |
| --- | --- | --- |
| `0` | *(nothing)* | 0 |
| `π/4` | `T` | 1 |
| `π/2` | `S` | 0 |
| `3π/4` | `S·T` | 1 |
| `π` | `Z` | 0 |
| `5π/4` | `Z·T` | 1 |
| `3π/2` | `S†` | 0 |
| `7π/4` | `T†` | 1 |
| anything else | `Rz(θ)` | — (needs synthesis) |

Note the last row: an `Rz` whose angle *happens* to be a multiple of `π/4` is
replaced by exact Clifford+T rather than being handed to a rotation
synthesizer, which is worth tens of `T` gates per rotation downstream.

## Correctness

The unitary is preserved exactly. `qizil verify` (and `--verify`) checks this
rather than assuming it:

- both modules are split into segments at every non-unitary instruction —
  measurements, resets, runtime calls, classical code, terminators;
- the segments must line up **textually**, so a rewrite cannot hide a changed
  measurement order or control flow behind a matching matrix;
- each gate run between fences is simulated and compared as a matrix.

Global phase is tracked, not discarded: `Module.global_phase` is the phase such
that `U_original == exp(i·global_phase) · U_output`, it is reported in the
summary, and `--preserve-global-phase` refuses any rewrite that would change it
(useful if the rewritten block is later lifted into a controlled form).

What the passes will **not** touch:

- anything across a basic block boundary — every rewrite is intra-block;
- anything across a measurement, reset, `__quantum__rt__*` call, unrecognized
  `__quantum__qis__*` gate, or a `__ctl`/`__ctladj` functor — these are
  scheduling barriers;
- any gate on a qubit pointer that is not a compile-time constant or a direct
  `__quantum__rt__qubit_allocate` result — an opaque pointer may alias anything,
  so nothing may move;
- rotations whose angle is an SSA value rather than a constant (dynamic angles
  usually come from measurement feedback);
- every line the passes did not rewrite, which is emitted **byte for byte** —
  `-O0` output is identical to the input.

The test suite (369 tests) includes ~250 randomized circuits over 2–4 qubits
checked against a reference simulator at every optimization level, plus the
same check on the tracked global phase.

## Results on the shipped examples

`qizil <example> -O3 --verify`:

| example | instructions | gates | depth | T-count | arbitrary rotations |
| --- | --- | --- | --- | --- | --- |
| `bell_redundant.ll` | 13 → 7 | 11 → 5 | 10 → 5 | 4 → 0 | 0 → 0 |
| `commuting_t.ll` | 12 → 7 | 10 → 5 | 10 → 6 | 4 → 0 | 0 → 0 |
| `adaptive_branch.ll` | 17 → 6 | 14 → 3 | 17 → 6 | 4 → 0 | 2 → 0 |
| `dynamic_qubits.ll` | 9 → 5 | 8 → 4 | 11 → 8 | 2 → 1 | 2 → 0 |
| `trotter_step.ll` | 104 → 76 | 100 → 72 | 53 → 44 | 0 → 0 | 44 → 34 |

All five verify as equivalent and pass LLVM's module verifier.
`trotter_step.ll` is four symmetric Trotter steps of a 4-spin transverse-field
Ising chain (`examples/gen_trotter.py`); the estimator reports −23% T states and
−22% runtime for it.

## Resource estimation

```console
$ qizil estimate examples/trotter_step.ll -O3

  metric                 before      after   change
  --------------------------------------------------
  logical qubits             15         15       0%
  code distance              11         11       0%
  logical depth             669        520   -22.3%
  T states                  660        510   -22.7%
  physical qubits         25410      25410       0%
  runtime (us)         2943.600   2288.000   -22.3%
```

The default `local` backend is an offline analytic surface-code model in the
style of the Azure Quantum Resource Estimator (arXiv:2211.07629): layout
overhead `2Q + ⌈√(8Q)⌉ + 1`, code distance from the threshold formula
`0.03·(p/0.01)^((d+1)/2)`, rotation synthesis at `0.53·log₂(1/ε) + 5.3` T gates
each, and an error budget split three ways. **The T-factory footprint uses a
simplified 15-to-1 model** and should be read as indicative; the delta between
two runs of the same model is the trustworthy part. For authoritative numbers,
`qizil.analysis.estimator.estimate_azure()` submits the module to the real
`microsoft.estimator` target.

## Architecture

```
  .ll / .bc                                                       .ll / .bc
      |                                                               ^
      v                                                               |
 +----------+     +-----------+     +--------------------+     +-------------+
 |  parser  | --> | instruction| -->|   pass pipeline    | --> |   emitter   |
 | (text,   |     |   model    |    | cancel             |     | (verbatim   |
 |  line-   |     | Module /   |    | merge-rotations    |     |  unless     |
 |  exact)  |     | Function / |    | commute            |     |  rewritten) |
 +----------+     | Block /    |    | clifford-t         |     +-------------+
      |           | Instruction|    +--------------------+
      |           +-----------+              ^
      |                 |                    |
      |                 v                    |
      |          +--------------+     +--------------+
      +--------> | BlockDag     | --> | commutation  |
                 | (qubit deps, |     | + alias      |
                 |  barriers)   |     |   analysis   |
                 +--------------+     +--------------+
```

| module | role |
| --- | --- |
| `qizil.ir.parser` | line-oriented LLVM IR parser; recognizes QIS calls, keeps everything else opaque |
| `qizil.ir.values` | operand model, qubit aliasing, LLVM double literals (including the `0x…` hex form) |
| `qizil.ir.gates` | the gate table: arities, axes, Hermiticity, normal forms |
| `qizil.ir.dag` | per-block dependency DAG, commutation rule, movement legality |
| `qizil.ir.module` | Module/Function/BasicBlock/Instruction + byte-exact emitter |
| `qizil.passes.*` | the four passes, the algebra they share, and the fixed-point driver |
| `qizil.analysis.*` | circuit metrics and the resource estimator |
| `qizil.verify.*` | reference simulator and equivalence checker |

## Limitations

Known, deliberate, and each one fails safe (the code is left alone):

- rewrites are intra-block; no cross-block or loop-level optimization;
- `__ctl` / `__ctladj` functors are opaque (their control operand is an
  `%Array*` Qizil does not model);
- `__quantum__qis__r__body(%Pauli, double, %Qubit*)` is treated as opaque
  rather than risk a wrong Pauli-enum mapping;
- rotations with symbolic (SSA) angles are never fused;
- no gate *decomposition* or *resynthesis* beyond exact Clifford+T runs — Qizil
  never expands a gate into a longer sequence to look for a win;
- the equivalence checker is a dense simulator: 8 qubits on the dependency-free
  backend, 12 with numpy installed;
- multi-line LLVM instructions are handled for bracketed forms (`switch`) only.

## License

MIT.

---

# Qizil — Türkçe

Qizil, **QIR** (Quantum Intermediate Representation) için yazılmış bir derleyici
optimizasyon modülüdür. `.ll` veya `.bc` biçimindeki QIR dosyasını okur, kuantum
komutlarından bir bağımlılık grafı (DAG) kurar, desen eşleştirmeli sadeleştirme
uygular ve optimize edilmiş QIR üretir. Klasik komutlar, temel bloklar, ölçümler
ve metadata **hiç dokunulmadan** korunur.

```console
qizil input.ll -O2 -o output.ll --verify
```

**Çalışma zamanı bağımlılığı yoktur.** Ayrıştırıcı, DAG, geçişler ve metrikler
saf Python standart kütüphanesiyle yazılmıştır. PyQIR yalnızca bitcode
okuma/yazma ve LLVM doğrulaması için, numpy yalnızca eşdeğerlik kontrolü için
isteğe bağlı olarak kullanılır.

### Optimizasyon geçişleri

| geçiş | `-O` | işlevi |
| --- | --- | --- |
| `cancel` | 1, 2, 3 | Çarpımı birim matris olan komşu kapı çiftlerini siler: `H·H`, `X·X`, `CNOT·CNOT`, `T·T†`, `Rz(θ)·Rz(−θ)` |
| `merge-rotations` | 1, 2, 3 | Aynı eksendeki ardışık rotasyonları birleştirir: `Rz(θ₁)·Rz(θ₂) → Rz(θ₁+θ₂)` |
| `commute` | 2, 3 | Değişmeli (commuting) kapıların arasından geçerek yukarıdaki iki dönüşümü tekrar arar |
| `clifford-t` | 2, 3 | Bir kubit üzerindeki aynı eksenli kapı dizisini toplayıp en ucuz Clifford+T karşılığını üretir — `T`-kapısı sayısını düşürür |

Değişme kuralı şudur: iki kapı, **paylaştıkları her kubit üzerinde** aynı Pauli
ekseni boyunca etki ediyorsa yer değiştirebilir. Örneğin `CNOT`'un kontrol
bacağı köşegen (`Z`) olduğu için `T · CNOT · T` ifadesi `CNOT · S` hâline gelir;
`T` sayısı 2'den 0'a iner.

### Doğruluk güvencesi

Devrenin üniter matrisi birebir korunur ve bu **varsayılmaz, kontrol edilir**:
`qizil verify` (veya `--verify`) iki modülü ölçüm/klasik komut sınırlarında
parçalara böler, sınır komutlarının metinsel olarak aynı kaldığını doğrular ve
her kapı dizisini bir referans simülatörle karşılaştırır. Global faz atılmaz;
`Module.global_phase` alanında izlenir ve raporlanır.

Şunlara asla dokunulmaz: temel blok sınırları, ölçümler, `__quantum__rt__*`
çağrıları, tanınmayan kapılar, `__ctl` fonktorları, statik olmayan kubit
işaretçileri ve sembolik (SSA) açılı rotasyonlar. Bunlar zamanlama bariyeri
olarak ele alınır. Yeniden yazılmayan her satır **bayt bayt** aynen çıktıya
aktarılır; `-O0` çıktısı girdinin birebir aynısıdır.

### Kaynak tahmini (Resource Estimator)

`qizil estimate input.ll -O2` komutu, optimizasyon öncesi ve sonrası için
fiziksel kubit sayısı, kod mesafesi, mantıksal derinlik, `T` durumu sayısı ve
çalışma süresi farkını raporlar. Varsayılan `local` arka ucu, Azure Quantum
Resource Estimator'ın yayımlanmış yüzey-kod modelini (arXiv:2211.07629) çevrimdışı
olarak uygular; `T`-fabrikası ayak izi basitleştirilmiş 15-to-1 modeliyle
hesaplandığı için yaklaşık değerdir. Kesin sonuç için
`qizil.analysis.estimator.estimate_azure()` modülü gerçek `microsoft.estimator`
hedefine gönderir.
