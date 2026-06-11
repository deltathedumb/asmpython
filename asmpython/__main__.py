"""CLI entry point so `python -m asmpython source.py [options]` works.

Delegates to the compiler front-end in `asmpython._compiler`.
"""

from __future__ import annotations

from ._compiler.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
