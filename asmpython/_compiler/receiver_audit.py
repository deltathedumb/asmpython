"""Locate the semantic rule enforcing receiver parameter spelling."""

from __future__ import annotations

import inspect

from .sema import SemaAnalyzer


def main() -> int:
    needle = "must take 'self' as its first parameter"
    found = 0
    for name, value in SemaAnalyzer.__dict__.items():
        if not callable(value):
            continue
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            continue
        if needle in source:
            found += 1
            print("METHOD", name)
            print(source)
    print("FOUND", found)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
