"""Bitcode (``.bc``) input/output and LLVM-level validation, via PyQIR.

Qizil's own pipeline is text-only and dependency-free.  PyQIR is used solely at
the edges: to convert bitcode to text on the way in, back to bitcode on the way
out, and to ask LLVM whether the module we emitted is well-formed.
"""

from __future__ import annotations

from ..ir.module import Module

__all__ = [
    "BITCODE_MAGIC",
    "is_bitcode",
    "have_pyqir",
    "bitcode_to_ll",
    "to_bitcode",
    "llvm_verify",
]

BITCODE_MAGIC = b"BC\xc0\xde"


def is_bitcode(data: bytes) -> bool:
    return data[:4] == BITCODE_MAGIC


def have_pyqir() -> bool:
    try:
        import pyqir  # noqa: F401
    except ImportError:
        return False
    return True


def _pyqir():
    try:
        import pyqir
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "reading or writing bitcode needs PyQIR: pip install 'qizil[bitcode]'"
        ) from exc
    return pyqir


def bitcode_to_ll(data: bytes, name: str = "") -> str:
    """Disassemble LLVM bitcode into textual IR."""
    pyqir = _pyqir()
    return str(pyqir.Module.from_bitcode(pyqir.Context(), data, name))


def to_bitcode(module: Module) -> bytes:
    """Assemble a Qizil module into LLVM bitcode."""
    pyqir = _pyqir()
    parsed = pyqir.Module.from_ir(pyqir.Context(), module.to_ll())
    return parsed.bitcode


def llvm_verify(text: str) -> str | None:
    """Return LLVM's complaint about ``text``, or ``None`` if it is valid.

    Returns a note instead of an error when PyQIR is not installed, so callers
    can treat this as a best-effort check.
    """
    if not have_pyqir():
        return None
    pyqir = _pyqir()
    try:
        parsed = pyqir.Module.from_ir(pyqir.Context(), text)
    except Exception as exc:  # pyqir raises ValueError with the LLVM message
        return str(exc)
    return parsed.verify()
