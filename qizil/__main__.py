"""Entry point for ``python -m qizil``.

Lets a fresh clone be run without installing anything::

    python -m qizil examples/bell_redundant.ll -O2 -o out.ll

which works on a stdlib-only Python, because the core has no dependencies.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
