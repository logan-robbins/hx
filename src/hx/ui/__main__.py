"""`python -m hx.ui --root <root>` / `python -m hx.ui --fixtures <dir>`."""

import sys

from hx.ui.server import main

if __name__ == "__main__":
    sys.exit(main())
