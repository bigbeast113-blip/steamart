"""Allows ``python3 -m steamart`` as well as ``python3 steamart.py``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
