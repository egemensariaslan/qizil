"""Everything the UI draws, assembled into one JSON-serializable payload.

The interactive server and the static report both call :func:`build`, so what
you see in the browser and what lands in a shared HTML file are the same
numbers produced by the same code path.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from ..analysis.circuit import describe
from ..analysis.estimator import compare, estimate
from ..api import optimize
from ..ir.bitcode import have_pyqir
from ..ir.parser import parse_ll

__all__ = ["build", "list_examples", "EXAMPLES_DIR"]

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"


def list_examples() -> list[dict]:
    """The bundled example circuits, when running from a checkout."""
    if not EXAMPLES_DIR.is_dir():
        return []
    out = []
    for path in sorted(EXAMPLES_DIR.glob("*.ll")):
        text = path.read_text(encoding="utf-8")
        title = ""
        for line in text.splitlines():
            if line.startswith(";") and "ModuleID" not in line:
                title = line.lstrip("; ").strip()
                break
        out.append({"name": path.name, "title": title, "source": text})
    return out


def _diff(before: str, after: str, limit: int = 4000) -> list[dict]:
    rows: list[dict] = []
    matcher = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in before.splitlines()[i1:i2]:
                rows.append({"kind": " ", "text": line})
        else:
            for line in before.splitlines()[i1:i2]:
                rows.append({"kind": "-", "text": line})
            for line in after.splitlines()[j1:j2]:
                rows.append({"kind": "+", "text": line})
        if len(rows) > limit:
            rows.append({"kind": " ", "text": f"... diff truncated at {limit} lines"})
            break
    return rows


#: Default pipeline time budget for the browser UI and standalone report --
#: the network-facing / untrusted-input surface. A generous ceiling: it
#: exists to guarantee a request eventually returns (with an honestly
#: reported, still fully correct, partial result -- see
#: qizil.api.optimize's time_budget_s), not to constrain everyday use.
DEFAULT_TIME_BUDGET_S = 25.0


def build(
    source: str,
    name: str = "<pasted>",
    level: int = 3,
    gateset: str = "auto",
    preserve_global_phase: bool = False,
    verify: bool = True,
    error_budget: float = 1e-3,
    qubit_params: str = "qubit_gate_ns_e3",
    time_budget_s: float | None = DEFAULT_TIME_BUDGET_S,
) -> dict:
    """Optimize ``source`` and package the whole story for a renderer.

    ``source`` is always literal QIR text here, never a filesystem path --
    every call site (the CLI's ``report`` command, and the HTTP server's
    pasted/uploaded module) has already resolved any file on disk into text
    before reaching this function.  Parsing it directly, instead of handing
    the raw string to :func:`qizil.api.optimize`, is deliberate: that
    function's own path-sniffing heuristic (a single line with no IR syntax
    looks like a filename) exists for CLI convenience and has no business
    running on a string that arrived over the ``qizil ui`` HTTP API --
    without this, a client could set ``source`` to an arbitrary filesystem
    path (``~/.ssh/id_rsa``, ``/etc/passwd``) and the server would try to
    read it off disk.
    """
    module = parse_ll(source)
    if not module.functions:
        raise ValueError(
            "no function definitions found -- this does not look like a QIR "
            "module (expected at least one `define ... { ... }`)"
        )
    result = optimize(
        module,
        level=level,
        gateset=gateset,
        preserve_global_phase=preserve_global_phase,
        verify=verify,
        llvm_check=have_pyqir(),
        time_budget_s=time_budget_s,
    )

    estimates = compare(
        estimate(result.original, error_budget, qubit_params),
        estimate(result.module, error_budget, qubit_params),
    )

    rewrites: list[dict] = []
    for stats in result.pipeline.per_pass.values():
        for note in stats.notes:
            rewrites.append({"pass": stats.name, "note": note})

    ir_before = result.original.to_ll()
    ir_after = result.to_ll()

    return {
        "name": name,
        "level": level,
        "options": {
            "gateset": gateset,
            "preserve_global_phase": preserve_global_phase,
            "error_budget": error_budget,
            "qubit_params": qubit_params,
        },
        "metrics": {"before": result.before.to_dict(), "after": result.after.to_dict()},
        "circuit": {
            "before": describe(result.original),
            "after": describe(result.module),
        },
        "pipeline": result.pipeline.to_dict(),
        "rewrites": rewrites,
        "verification": (
            result.verification.to_dict() if result.verification is not None else None
        ),
        "llvm": {
            "checked": have_pyqir(),
            "diagnostic": result.llvm_diagnostic,
        },
        "global_phase": result.global_phase,
        "estimate": {
            "before": estimates.before.to_dict(),
            "after": estimates.after.to_dict(),
            "comparison": estimates.to_dict(),
        },
        "ir": {"before": ir_before, "after": ir_after},
        "diff": _diff(ir_before, ir_after),
    }


def load_source(path: str) -> tuple[str, str]:
    """Read IR text from a path, returning ``(display_name, text)``."""
    from ..api import read_source

    return os.path.basename(path), read_source(path)
