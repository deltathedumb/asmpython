"""Locate the semantic rule enforcing receiver parameter spelling."""

from __future__ import annotations

import inspect

from .sema import SemaAnalyzer


def main() -> int:
    found = 0
    for name, value in SemaAnalyzer.__dict__.items():
        if not callable(value):
            continue
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            continue
        normalized = source.lower()
        if (
            "first parameter" in normalized
            or "params[0]" in normalized
            or "params[0] != \"self\"" in source
            or "params[0] != 'self'" in source
        ):
            found += 1
            print("METHOD", name)
            print(source)
    print("FOUND", found)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
