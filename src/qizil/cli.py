"""Command line interface.

    qizil input.ll -O2 -o output.ll        # the headline form
    qizil stats input.ll
    qizil estimate input.ll -O2
    qizil verify before.ll after.ll
    qizil dag input.ll --dot
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .analysis.estimator import QUBIT_PRESETS, compare, estimate
from .analysis.metrics import measure
from .api import optimize, parse, read_source
from .ir.bitcode import have_pyqir, to_bitcode
from .ir.dag import BlockDag
from .passes import PASS_REGISTRY, PIPELINES

__all__ = ["main", "build_parser"]

SUBCOMMANDS = (
    "optimize",
    "stats",
    "estimate",
    "verify",
    "report",
    "ui",
    "dag",
    "passes",
)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _pct(before: float, after: float) -> str:
    if before == 0:
        return "     -"
    change = (after - before) / before * 100.0
    if abs(change) < 0.05:
        return "    0%"
    return f"{change:+6.1f}%"


def _table(rows: list[tuple[str, object, object]], headers=("before", "after")) -> str:
    width = max((len(r[0]) for r in rows), default=6)
    out = [f"  {'metric':<{width}}  {headers[0]:>9}  {headers[1]:>9}  {'change':>7}"]
    out.append("  " + "-" * (width + 32))
    for label, before, after in rows:
        out.append(
            f"  {label:<{width}}  {before:>9}  {after:>9}  {_pct(float(before), float(after)):>7}"
        )
    return "\n".join(out)


def _metric_rows(before, after) -> list[tuple[str, object, object]]:
    return [
        ("qubits", before.qubits, after.qubits),
        ("quantum instructions", before.total_instructions, after.total_instructions),
        ("gates", before.gates, after.gates),
        ("1-qubit gates", before.one_qubit_gates, after.one_qubit_gates),
        ("2-qubit gates", before.two_qubit_gates, after.two_qubit_gates),
        ("T gates", before.t_gates, after.t_gates),
        ("pi/4 rotations", before.quarter_rotations, after.quarter_rotations),
        ("arbitrary rotations", before.arbitrary_rotations, after.arbitrary_rotations),
        ("exact T-count", before.t_count, after.t_count),
        ("depth", before.depth, after.depth),
        ("measurements", before.measurements, after.measurements),
    ]


def _histogram(before, after) -> str:
    keys = sorted(set(before.gate_histogram) | set(after.gate_histogram))
    parts = []
    for key in keys:
        b = before.gate_histogram.get(key, 0)
        a = after.gate_histogram.get(key, 0)
        parts.append(f"{key} {b}->{a}" if b != a else f"{key} {a}")
    return ", ".join(parts)


# --------------------------------------------------------------------------
# optimize
# --------------------------------------------------------------------------


def cmd_optimize(args: argparse.Namespace) -> int:
    result = optimize(
        args.input,
        level=args.opt_level,
        passes=args.passes.split(",") if args.passes else None,
        disable=args.disable.split(",") if args.disable else None,
        gateset=args.gateset,
        preserve_global_phase=args.preserve_global_phase,
        tolerance=args.tolerance,
        max_iterations=args.max_iterations,
        verify=args.verify,
        llvm_check=args.llvm_check,
    )

    if args.json:
        if args.output and args.output != "-":
            _write_output(args, result)
        print(json.dumps(result.to_dict(), indent=2))
        return _exit_code(result, args)

    if args.output and args.output != "-":
        _write_output(args, result)
        stream = sys.stdout
    else:
        sys.stdout.write(_emit_text(args, result))
        stream = sys.stderr

    if not args.quiet:
        print(_summary(result, args), file=stream)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2)
        print(f"  report written to {args.report}", file=stream)
    return _exit_code(result, args)


def _emit_text(args, result) -> str:
    if args.emit == "bc":
        raise SystemExit("qizil: --emit bc requires -o FILE (bitcode is binary)")
    return result.to_ll()


def _write_output(args, result) -> None:
    if args.emit == "bc" or args.output.endswith(".bc"):
        if not have_pyqir():
            raise SystemExit(
                "qizil: writing bitcode needs PyQIR: pip install 'qizil[bitcode]'"
            )
        with open(args.output, "wb") as fh:
            fh.write(to_bitcode(result.module))
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(result.to_ll())


def _summary(result, args) -> str:
    lines = [
        "",
        f"qizil {__version__}  {result.module.source_name}  (-O{result.level})",
        "",
        _table(_metric_rows(result.before, result.after)),
        "",
    ]
    applied = [
        f"{s.name} x{s.rewrites}"
        for s in result.pipeline.per_pass.values()
        if s.rewrites
    ]
    lines.append(
        f"  passes: {', '.join(applied) if applied else 'no rewrites applied'}"
        f"  ({result.pipeline.iterations} iteration"
        f"{'s' if result.pipeline.iterations != 1 else ''})"
    )
    lines.append(f"  gates:  {_histogram(result.before, result.after)}")
    if result.global_phase:
        lines.append(
            f"  phase:  output carries an unobservable global phase of "
            f"{result.global_phase:+.6f} rad"
        )
    if result.verification is not None:
        v = result.verification
        if v.ok:
            lines.append(
                f"  verify: unitary preserved over {v.checked_segments} segment(s), "
                f"max error {v.max_error:.2e}"
            )
        elif not v.available:
            lines.append(f"  verify: skipped - {'; '.join(v.messages)}")
        else:
            lines.append("  verify: FAILED")
            for message in v.messages:
                lines.append(f"          {message}")
    if result.llvm_diagnostic:
        lines.append(f"  llvm:   {result.llvm_diagnostic}")
    elif args.llvm_check:
        lines.append("  llvm:   module verified")
    if args.verbose:
        for stats in result.pipeline.per_pass.values():
            for note in stats.notes:
                lines.append(f"    [{stats.name}] {note}")
    lines.append("")
    return "\n".join(lines)


def _exit_code(result, args) -> int:
    verification = result.verification
    if verification is not None and not verification.ok and verification.available:
        return 2
    if result.llvm_diagnostic:
        return 3
    return 0


# --------------------------------------------------------------------------
# stats / estimate / verify / dag / passes
# --------------------------------------------------------------------------


def cmd_stats(args: argparse.Namespace) -> int:
    module = parse(args.input)
    m = measure(module)
    if args.json:
        print(json.dumps(m.to_dict(), indent=2))
        return 0
    print(f"\nqizil {__version__}  {module.source_name}\n")
    width = max(len(k) for k in m.to_dict())
    for key, value in m.to_dict().items():
        if key == "gate_histogram":
            continue
        print(f"  {key:<{width}}  {value:>9}")
    print(f"\n  gates: {', '.join(f'{k} x{v}' for k, v in sorted(m.gate_histogram.items()))}\n")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    if args.no_optimize:
        module = parse(args.input)
        est = estimate(module, args.error_budget, args.qubit_params)
        if args.json:
            print(json.dumps(est.to_dict(), indent=2))
            return 0
        print(_estimate_block(module.source_name, est))
        return 0

    result = optimize(
        args.input,
        level=args.opt_level,
        gateset=args.gateset,
        preserve_global_phase=args.preserve_global_phase,
    )
    comparison = compare(
        estimate(result.original, args.error_budget, args.qubit_params),
        estimate(result.module, args.error_budget, args.qubit_params),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "before": comparison.before.to_dict(),
                    "after": comparison.after.to_dict(),
                    "comparison": comparison.to_dict(),
                },
                indent=2,
            )
        )
        return 0

    before, after = comparison.before, comparison.after
    rows = [
        ("algorithmic qubits", before.algorithmic_qubits, after.algorithmic_qubits),
        ("logical qubits", before.logical_qubits, after.logical_qubits),
        ("code distance", before.code_distance, after.code_distance),
        ("logical depth", before.logical_depth, after.logical_depth),
        ("T states", before.t_states, after.t_states),
        (
            "physical qubits",
            before.physical_qubits,
            after.physical_qubits,
        ),
        (
            "runtime (us)",
            f"{before.runtime_ns / 1000:.3f}",
            f"{after.runtime_ns / 1000:.3f}",
        ),
    ]
    print(f"\nqizil {__version__}  resource estimate  {result.module.source_name}  (-O{result.level})")
    print(f"  backend={before.backend}  qubit_params={before.qubit_params}  "
          f"error_budget={before.error_budget}\n")
    print(_table(rows))
    print()
    for note in after.model_notes:
        print(f"  note: {note}")
    print()
    return 0


def _estimate_block(name: str, est) -> str:
    lines = [f"\nqizil {__version__}  resource estimate  {name}\n"]
    data = est.to_dict()
    data.pop("metrics", None)
    notes = data.pop("model_notes", [])
    width = max(len(k) for k in data)
    for key, value in data.items():
        if isinstance(value, float):
            value = f"{value:.6g}"
        lines.append(f"  {key:<{width}}  {value:>14}")
    for note in notes:
        lines.append(f"\n  note: {note}")
    lines.append("")
    return "\n".join(lines)


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify_equivalence

    a, b = parse(args.original), parse(args.optimized)
    result = verify_equivalence(a, b, tol=args.tolerance)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif not result.available:
        print(f"\n  CANNOT VERIFY: {'; '.join(result.messages)}\n")
        return 1
    else:
        status = "EQUIVALENT" if result.ok else "NOT EQUIVALENT"
        print(f"\n  {status}")
        print(f"  segments checked : {result.checked_segments}")
        print(f"  segments skipped : {result.skipped_segments}")
        print(f"  max error        : {result.max_error:.3e}")
        print(f"  global phase     : {result.global_phase:+.6f} rad")
        for message in result.messages:
            print(f"  - {message}")
        print()
    if result.ok:
        return 0
    return 2 if result.available else 1


def cmd_dag(args: argparse.Namespace) -> int:
    module = parse(args.input)
    printed = 0
    for fn, block in module.blocks():
        dag = BlockDag(block)
        if not dag.nodes:
            continue
        if args.block and block.label != args.block:
            continue
        name = f"{fn.name}.{block.label or 'entry'}"
        if args.dot:
            print(dag.to_dot(name))
        else:
            print(f"\n  @{name}  ({len(dag.nodes)} nodes, depth {dag.depth()})")
            for node in dag.nodes:
                preds = ",".join(f"n{p}" for p in sorted(node.preds)) or "-"
                kind = "barrier" if node.universal else "op"
                print(f"    n{node.id:<3} {kind:<8} {node.label:<32} <- {preds}")
        printed += 1
    if not printed:
        print("  (no quantum instructions found)")
    elif not args.dot:
        print()
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui.server import serve

    serve(
        host=args.host,
        port=args.port,
        open_browser=not args.no_browser,
        quiet=not args.verbose,
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .ui.payload import build
    from .ui.server import render_page

    source = read_source(args.input)
    name = args.input if "\n" not in args.input else "<stdin>"
    data = build(
        source,
        name=os.path.basename(name),
        level=args.opt_level,
        gateset=args.gateset,
        preserve_global_phase=args.preserve_global_phase,
        verify=not args.no_verify,
        error_budget=args.error_budget,
        qubit_params=args.qubit_params,
    )
    data["version"] = __version__
    html = render_page(data)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html)
    size = len(html.encode("utf-8")) / 1024
    print(f"  report written to {args.output}  ({size:.0f} KB, self-contained)")
    verification = data.get("verification")
    if verification and not verification["equivalent"] and verification["available"]:
        return 2
    return 0


def cmd_passes(args: argparse.Namespace) -> int:
    print("\n  available passes\n")
    width = max(len(n) for n in PASS_REGISTRY)
    for name, cls in PASS_REGISTRY.items():
        print(f"    {name:<{width}}  {cls.description}")
    print("\n  pipelines\n")
    for level, names in PIPELINES.items():
        print(f"    -O{level}  {', '.join(names) if names else '(no optimization)'}")
    print()
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def _add_opt_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-O",
        "--opt-level",
        dest="opt_level",
        type=int,
        nargs="?",
        const=2,
        default=2,
        choices=[0, 1, 2, 3],
        help="optimization level (default: 2)",
    )
    parser.add_argument(
        "--gateset",
        choices=["auto", "strict"],
        default="auto",
        help="auto may introduce core gates (S/T/Z/Rz); strict re-emits only "
        "gates already used by the input",
    )
    parser.add_argument(
        "--preserve-global-phase",
        action="store_true",
        help="reject rewrites that change the global phase",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qizil",
        description="QIR-Opt: optimize Quantum Intermediate Representation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:  qizil input.ll -O2 -o output.ll",
    )
    parser.add_argument("--version", action="version", version=f"qizil {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    opt = subparsers.add_parser("optimize", help="optimize a QIR module (default)")
    opt.add_argument("input", help="input .ll or .bc file ('-' for stdin)")
    opt.add_argument("-o", "--output", help="output file (default: stdout)")
    opt.add_argument(
        "--emit", choices=["ll", "bc"], default="ll", help="output format"
    )
    _add_opt_flags(opt)
    opt.add_argument("--passes", help="comma-separated pass list (overrides -O)")
    opt.add_argument("--disable", help="comma-separated passes to skip")
    opt.add_argument(
        "--max-iterations", type=int, help="cap pipeline repetitions"
    )
    opt.add_argument(
        "--tolerance", type=float, default=1e-9, help="angle comparison tolerance"
    )
    opt.add_argument(
        "--verify",
        action="store_true",
        help="check unitary equivalence against the input (needs numpy)",
    )
    opt.add_argument(
        "--llvm-check",
        action="store_true",
        help="run the output through LLVM's verifier (needs PyQIR)",
    )
    opt.add_argument("--report", help="write a JSON optimization report to FILE")
    opt.add_argument("-q", "--quiet", action="store_true", help="suppress the summary")
    opt.add_argument(
        "-v", "--verbose", action="store_true", help="list every rewrite applied"
    )
    _add_common(opt)
    opt.set_defaults(func=cmd_optimize)

    stats = subparsers.add_parser("stats", help="report circuit metrics")
    stats.add_argument("input")
    _add_common(stats)
    stats.set_defaults(func=cmd_stats)

    est = subparsers.add_parser(
        "estimate", help="fault-tolerant resource estimate, before vs after"
    )
    est.add_argument("input")
    _add_opt_flags(est)
    est.add_argument(
        "--error-budget", type=float, default=1e-3, help="total error budget"
    )
    est.add_argument(
        "--qubit-params",
        default="qubit_gate_ns_e3",
        choices=sorted(QUBIT_PRESETS),
        help="physical qubit model",
    )
    est.add_argument(
        "--no-optimize",
        action="store_true",
        help="estimate the input as-is instead of comparing",
    )
    _add_common(est)
    est.set_defaults(func=cmd_estimate)

    ver = subparsers.add_parser("verify", help="check two modules are equivalent")
    ver.add_argument("original")
    ver.add_argument("optimized")
    ver.add_argument("--tolerance", type=float, default=1e-8)
    _add_common(ver)
    ver.set_defaults(func=cmd_verify)

    dag = subparsers.add_parser("dag", help="print the quantum instruction graph")
    dag.add_argument("input")
    dag.add_argument("--dot", action="store_true", help="emit Graphviz DOT")
    dag.add_argument("--block", help="only this basic block label")
    _add_common(dag)
    dag.set_defaults(func=cmd_dag)

    report = subparsers.add_parser(
        "report", help="write a standalone HTML report (charts, circuit, proof)"
    )
    report.add_argument("input")
    report.add_argument(
        "-o", "--output", default="qizil-report.html", help="output HTML file"
    )
    _add_opt_flags(report)
    report.add_argument(
        "--error-budget", type=float, default=1e-3, help="resource estimate budget"
    )
    report.add_argument(
        "--qubit-params",
        default="qubit_gate_ns_e3",
        choices=sorted(QUBIT_PRESETS),
        help="physical qubit model",
    )
    report.add_argument(
        "--no-verify", action="store_true", help="skip the equivalence check"
    )
    report.set_defaults(func=cmd_report)

    ui = subparsers.add_parser("ui", help="open the browser UI")
    ui.add_argument("--port", type=int, default=8731)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser")
    ui.add_argument("-v", "--verbose", action="store_true", help="log requests")
    ui.set_defaults(func=cmd_ui)

    passes = subparsers.add_parser("passes", help="list passes and pipelines")
    passes.set_defaults(func=cmd_passes)

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """Allow ``qizil input.ll -O2 -o out.ll`` without naming the subcommand."""
    for arg in argv:
        if arg in ("-h", "--help", "--version"):
            return argv
        if arg.startswith("-") and arg != "-":
            continue
        return argv if arg in SUBCOMMANDS else ["optimize", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not argv:
        parser.print_help()
        return 1
    args = parser.parse_args(_normalize_argv(argv))
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    if getattr(args, "input", None) == "-":
        args.input = sys.stdin.read()
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"qizil: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
