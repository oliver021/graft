"""
Root conftest — adds all package roots to sys.path once.

Every test file in any subdirectory gets correct imports automatically.
No more per-file sys.path.insert() blocks.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

for _pkg in ("code_indexer", "parser", "engine", "server", "cli"):
    _p = ROOT / _pkg
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
