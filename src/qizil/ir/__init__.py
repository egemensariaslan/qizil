"""QIR ingestion: parsing, the instruction model, and the dependency DAG."""

from .dag import BlockDag, commutes
from .module import (
    BasicBlock,
    Function,
    InstKind,
    Instruction,
    Module,
    QuantumOp,
)
from .parser import ParseError, parse_file, parse_ll

__all__ = [
    "Module",
    "Function",
    "BasicBlock",
    "Instruction",
    "InstKind",
    "QuantumOp",
    "BlockDag",
    "commutes",
    "parse_ll",
    "parse_file",
    "ParseError",
]
