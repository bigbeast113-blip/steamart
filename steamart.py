#!/usr/bin/env python3
"""SteamArt launcher.

Run with no arguments to open the web interface:  python3 steamart.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from steamart.cli import main  # noqa: E402

if __name__ == "__main__":
    if sys.version_info < (3, 7):
        sys.exit("SteamArt needs Python 3.7 or newer (found %d.%d)." % sys.version_info[:2])
    sys.exit(main())
