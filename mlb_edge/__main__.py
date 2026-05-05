"""
Package entry point — lets the entire pipeline be invoked as

    python -m mlb_edge
    python -m mlb_edge 2026-04-27
    python -m mlb_edge --bets-only

This is functionally identical to running the top-level ``predict.py``
script; we forward straight to it so behavior stays in one place.
"""
from __future__ import annotations

import os
import sys


def _main() -> int:
    # Add the repo root (parent of this package) to sys.path so we can import
    # the sibling top-level ``predict`` module regardless of how the user
    # invoked us (cwd may be anywhere).
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import predict  # type: ignore[import-not-found]
    return predict.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
