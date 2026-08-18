# Changelog

All notable changes to Qizil are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has
not yet made a tagged release, so everything so far is under `[Unreleased]`.

## [Unreleased]

### Added

- Core optimizer: `cancel`, `merge-rotations`, `commute`, `clifford-t` passes
  over a quantum instruction DAG, with `-O0`–`-O3` pipelines.
- Dependency-free equivalence checker (`qizil verify`, `--verify`) with a
  numpy-accelerated backend used automatically when available.
- Fault-tolerant resource estimator (`qizil estimate`) after
  [arXiv:2211.07629](https://arxiv.org/abs/2211.07629), plus an optional
  Azure Quantum Resource Estimator backend.
- CLI (`optimize`/`stats`/`estimate`/`verify`/`dag`/`report`/`ui`/`passes`)
  and a Python API (`qizil.optimize`).
- Browser UI (`qizil ui`) and standalone HTML reports (`qizil report`):
  circuit diagrams, before/after metrics, the fault-tolerant resource
  comparison, a rewrite trace, and the equivalence proof, all as one
  hand-written page with no framework or build step.
- Six example circuits (`examples/*.ll`), including a Quantum Fourier
  Transform immediately followed by its own exact inverse
  (`qft_roundtrip.ll`) — a closed-form correctness demonstration independent
  of the tool itself; see `docs/VALIDATION.md`.
- `./qizil` executable at the repo root: runs from a bare clone with the
  standard library alone, no install step.
- GitHub Actions CI: the test matrix across Python 3.10–3.13 on
  Linux/macOS/Windows, including a job with no optional dependencies
  installed, plus lint and a package-build-and-install smoke test.
- `time_budget_s` / `--time-budget`: an optional wall-clock budget on the
  optimization pipeline. A truncated run is always still fully correct
  (every individual rewrite preserves the unitary on its own), only
  possibly less optimized.

### Fixed

- `CliffordTPass` restarted its scan from the top of the block after every
  rewrite, making it effectively cubic for long blocks — rewritten as a
  single left-to-right sweep. 21x faster on a 5,000-gate benchmark. See
  `docs/BENCHMARKS.md`.
- The numpy-accelerated equivalence checker applied each gate via a dense
  `dim x dim` matrix multiply, `O(dim^3)` per gate — rewritten to a
  tensor-contraction local update, `O(dim^2)` per gate, up to 71x faster at
  12 qubits. See `docs/BENCHMARKS.md`.
- `verify_equivalence` could report `ok=True` with zero segments actually
  checked (every segment skipped for exceeding a size limit) — now reports
  `ok=False, available=False` in that case; a module with genuinely no
  quantum content is unaffected.
- The UI server's `/api/optimize` endpoint let a client's `source` field be
  interpreted as a filesystem path via the same heuristic used for CLI
  arguments — closed; the server now always treats `source` as literal text.
- The circuit diagram and the metrics panel both labeled a value "depth"
  while computing genuinely different quantities (a rendering-layout column
  count vs. the DAG's true dependency depth) — relabeled to remove the
  ambiguity.
