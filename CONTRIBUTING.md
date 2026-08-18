# Contributing to Qizil

## Getting started

```console
git clone <this repo> && cd qizil
pip install -e '.[dev]'
pytest -q          # 484 tests, ~10s
```

No install is required just to *run* Qizil (`./qizil ...` from a clone works
with the standard library alone) — `pip install -e '.[dev]'` is for
development: it adds numpy, PyQIR, ruff and pytest.

## Before opening a PR

```console
pytest -q                                  # full suite
ruff check src/ tests/ examples/           # lint
pytest tests/test_semantics_pure.py -q     # the equivalence proof, no numpy
```

CI runs the same checks across Python 3.10–3.13, Linux/macOS/Windows, and
once with no optional dependencies installed at all — a change that only
works with numpy present will fail there even if it passes locally with
numpy installed.

## What review actually checks for

This project's central claim is that every rewrite preserves the circuit's
unitary exactly (see `docs/VALIDATION.md`). That claim is only as strong as
the code enforcing it, so:

- **A change to any pass** (`src/qizil/passes/`) needs the randomized
  equivalence suite to keep passing, and if it adds a new kind of rewrite,
  needs the relevant gate(s) added to `tests/conftest.py`'s
  `random_gates` so the fuzzer actually exercises it. See
  `docs/extending.md` for the exact checklist (barriers, definite qubits,
  phase bookkeeping, idempotence).
- **A change to the reference simulator** (`src/qizil/verify/`) needs
  cross-checking against the *other* backend, not just against its own
  prior behavior — see `tests/test_accelerated.py` for the pattern (an
  earlier rewrite of the numpy backend was wrong in a way that looked
  completely plausible and was only caught by an exhaustive cross-check
  against the independently-implemented pure-Python backend; that story is
  in the module's own docstring and in `docs/BENCHMARKS.md`).
- **A performance-sensitive change** should include a before/after number,
  the way `docs/BENCHMARKS.md` does for the two real regressions found
  there. "It should be faster" without a measurement is not evidence.
- **A UI change** should follow the existing design system
  (`docs/design-system.md`) — one accent color for state, category colors
  reserved for gate semantics, no new copy that doesn't carry information
  (see that doc's rule 7).

## Adding a gate or a pass

Covered in full in [`docs/extending.md`](docs/extending.md), including the
exact fields a new `GateSpec` needs and the invariants a new pass must not
break.

## Reporting a bug

Open an issue with the input `.ll`/`.bc` file (or a minimal repro) and the
command you ran. If it's a correctness issue — Qizil produced a module that
is not actually equivalent to the input — `qizil verify before.ll after.ll`
output is the single most useful thing to include.

For security issues specifically, see [`SECURITY.md`](SECURITY.md) instead
of a public issue.

## License

Contributions are accepted under the project's [MIT license](LICENSE).
