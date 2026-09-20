"""`python -m hx` — the same entry point as the installed `hx` script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
