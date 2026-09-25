"""
Allows `python -m motionextract` as well as the installed `motionextract`
command. Useful when the interpreter's Scripts directory is not on PATH,
which is a common default on Windows.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
