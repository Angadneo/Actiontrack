"""Entry point for ``python -m actiontrack``."""

import sys

from actiontrack.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
