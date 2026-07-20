"""Entry point for ``python -m qizil`` once the package is installed.

To run from a clone without installing anything, use the ``./qizil`` script at
the repository root instead.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
