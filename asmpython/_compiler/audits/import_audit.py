"""Diagnose project modules silently skipped by whole-program loading.

This is a developer tool, not part of generated programs. It walks the same
project import graph as :mod:`asmpython._compiler.program`, parses every
reachable source file with the active compiler parser, and reports the exact
path and exception for any module that would otherwise disappear behind a
later unresolved-symbol diagnostic.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..lexer import Lexer
from ..parser import Parser
from ..program import _project_imports, _project_root


def audit_import_graph(entry_path: Path) -> list[tuple[Path, Exception]]:
    entry = entry_path.resolve()
    root = _project_root(entry)
    queue: list[Path] = [entry]
    seen: set[str] = set()
    failures: list[tuple[Path, Exception]] = []

    print("ENTRY", entry)
    print("ROOT", root)

    while queue:
        path = queue.pop(0).resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)

        try:
            source = path.read_text(encoding="utf-8")
            module = Parser(Lexer(source).tokenize()).parse()
        except Exception as error:
            failures.append((path, error))
            print(
                "FAIL",
                path,
                type(error).__name__ + ":",
                str(error),
            )
            continue

        print(
            "PASS",
            path,
            "functions=" + str(len(module.funcs)),
            "classes=" + str(len(module.classes)),
        )
        for imported in _project_imports(module, path, root):
            resolved = imported.resolve()
            if str(resolved) not in seen:
                queue.append(resolved)

    print("MODULES", len(seen))
    print("FAILURES", len(failures))
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)
    failures = audit_import_graph(args.entry)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
