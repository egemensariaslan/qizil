# Security policy

## Reporting a vulnerability

Email the maintainer (see `pyproject.toml`'s `authors` field) rather than
opening a public issue. Include the input that triggers the problem and, if
it's a memory/resource issue, the platform and Python version.

## Threat model

Qizil is a local command-line compiler and an optional local web UI, not a
hosted service. Two things are worth being explicit about:

- **`qizil ui` binds to `127.0.0.1` by default** — loopback only, not
  reachable from the network. `--host` can override this; doing so is the
  operator's choice to make, not something Qizil does on its own.
- **The `/api/optimize` endpoint accepts arbitrary QIR text and options.**
  It parses untrusted input (any `.ll`/`.bc` text the browser sends,
  including pasted or uploaded content) and runs the optimization pipeline
  and reference simulator on it. It does *not* execute the input as code —
  QIR text is data, parsed by a hand-written line classifier
  (`qizil.ir.parser`), never `eval`'d, and the reference simulator's gate
  matrices are a fixed table, not derived from the input in any way that
  could escape the sandbox of "produce a matrix."

## What's already been hardened, and why it's listed here

Concretely, not as a general assurance:

- **Local-file-read via the `source` field.** `qizil.api.optimize` has a
  path-sniffing heuristic for CLI convenience (a single line with no IR
  syntax is treated as a filename to open). That heuristic was, until it
  was found and fixed, still reachable through the UI server's `source`
  field — a client could set `source` to `~/.ssh/id_rsa` or `/etc/hosts`
  and the server would read that file's contents and use them as the
  module to optimize. `qizil.ui.payload.build` now always parses `source`
  as literal text (`parse_ll` directly), bypassing that heuristic entirely,
  for every caller of that function, not just the server.
- **Request size and time bounds.** `/api/optimize` rejects bodies over 8MB
  before parsing them. The optimization pipeline accepts an optional
  wall-clock budget (`time_budget_s`), defaulted to 25 seconds for the UI
  server and `qizil report` specifically (the untrusted-input surface) —
  a truncated run is always still a fully correct QIR module, only
  possibly less optimized, so this cannot turn a slow input into a wrong
  answer, only into a bounded-time one.
- **The reference simulator declines rather than hangs** on a segment too
  large to verify in reasonable time (a coarse, documented cost heuristic
  in both `qizil.verify.accelerated` and `qizil.verify.simulator`),
  reporting `available=False` rather than blocking indefinitely.

## What's explicitly out of scope

- Malicious LLVM bitcode (`.bc`) parsing is delegated entirely to PyQIR
  (which wraps LLVM's own bitcode reader) when the `bitcode` extra is
  installed; Qizil does not parse bitcode itself. Vulnerabilities in LLVM's
  bitcode reader are LLVM's / PyQIR's to fix, not this project's.
- Qizil never executes the circuits it optimizes on real quantum hardware
  or a cloud service, except when the user explicitly opts into the Azure
  Quantum Resource Estimator backend (`azure-quantum`, a separate optional
  dependency, using the user's own Azure credentials) — that traffic is
  between the user's machine and Azure directly, not proxied through Qizil.
