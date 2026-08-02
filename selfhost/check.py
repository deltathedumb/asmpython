"""Self-host progress gauntlet.

The July milestone is *self-compilation*: asmpython compiling asmpython. This
script measures the distance to that goal by running each phase of the
compiler front-end over the compiler's own source and reporting the first
construct that chokes, per file.

It checks three gates, in order, and stops a file at the first one it fails:

    LEX    -> the tokenizer accepts the file
    PARSE  -> the recursive-descent parser builds an AST
    SEMA   -> semantic analysis accepts the AST

SEMA is run in a *lenient* mode: asmpython has no cross-file module resolution
yet, so names defined in sibling modules (`SourcePos`, `Token`, ...) would
otherwise read as "undefined". Lenient mode seeds the scope with every
top-level name the file *imports* and *defines* so that the analyser can get
past cross-module references and surface the genuinely-unsupported language
constructs instead. This is a measurement aid, not a claim that the file
would link.

Usage:
    python -m selfhost.check            # summary table + first blocker per file
    python -m selfhost.check --verbose  # include full error text
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asmpython._compiler.lexer import Lexer  # noqa: E402
from asmpython._compiler.parser import Parser  # noqa: E402
from asmpython._compiler.sema import analyze as sema_analyze  # noqa: E402
from asmpython._compiler.errors import CompileError  # noqa: E402


# The compiler's own source, in rough dependency order (leaves first).
TARGETS = [
    "legacy/asmpython/__init__.py",
    "legacy/asmpython/__main__.py",
    "legacy/asmpython/stdlib/assembly/__init__.py",
    "legacy/asmpython/stdlib/__init__.py",
    "legacy/asmpython/stdlib/math.py",
    "legacy/asmpython/stdlib/os.py",
    "legacy/asmpython/_compiler/__init__.py",
    "legacy/asmpython/_compiler/errors.py",
    "legacy/asmpython/_compiler/ast_nodes.py",
    "legacy/asmpython/_compiler/lexer.py",
    "legacy/asmpython/_compiler/parser.py",
    "legacy/asmpython/_compiler/sema.py",
    "legacy/asmpython/_compiler/codegen.py",
    "legacy/asmpython/_targets/target_linux.py",
    "legacy/asmpython/_targets/target_windows.py",
    "legacy/asmpython/_compiler/driver.py",
    "legacy/asmpython/_compiler/__main__.py",
    "legacy/asmpython/_runtime/__init__.py",
    "legacy/asmpython/_runtime/build.py",
]


class Gate:
    LEX = "LEX"
    PARSE = "PARSE"
    SEMA = "SEMA"
    OK = "OK"


def _format_compile_error(e: CompileError, path: Path) -> str:
    pos = getattr(e, "pos", None)
    where = f"{path}:{pos.line}:{pos.col}" if pos else str(path)
    return f"{where}: {type(e).__name__}: {e.args[0] if e.args else e}"


def check_file(path: Path, *, verbose: bool) -> tuple[str, str]:
    """Return (gate_reached, detail). gate_reached == Gate.OK means all passed."""
    src = path.read_text(encoding="utf-8")

    # ---- LEX ----
    try:
        tokens = Lexer(src).tokenize()
    except CompileError as e:
        return Gate.LEX, _format_compile_error(e, path)
    except Exception as e:  # noqa: BLE001
        return Gate.LEX, f"{path}: crash: {e!r}"

    # ---- PARSE ----
    try:
        module = Parser(tokens).parse()
    except CompileError as e:
        return Gate.PARSE, _format_compile_error(e, path)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc() if verbose else ""
        return Gate.PARSE, f"{path}: crash: {e!r}\n{tb}"

    # ---- SEMA (lenient: pre-bind cross-module names) ----
    try:
        sema_analyze(module)
    except CompileError as e:
        return Gate.SEMA, _format_compile_error(e, path)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc() if verbose else ""
        return Gate.SEMA, f"{path}: crash: {e!r}\n{tb}"

    return Gate.OK, ""


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    rows: list[tuple[str, str, str]] = []
    for rel in TARGETS:
        path = ROOT / rel
        if not path.exists():
            rows.append((rel, "MISSING", ""))
            continue
        gate, detail = check_file(path, verbose=verbose)
        rows.append((rel, gate, detail))

    # Gate at which each file currently stops. The "frontier" is the first
    # file that doesn't reach OK.
    name_w = max(len(r[0]) for r in rows)
    print("self-host front-end gauntlet (lex -> parse -> sema)\n")
    n_ok = 0
    frontier_detail = None
    for rel, gate, detail in rows:
        mark = "OK  " if gate == Gate.OK else "STOP"
        reached = "all gates" if gate == Gate.OK else f"stopped at {gate}"
        print(f"  [{mark}] {rel.ljust(name_w)}  {reached}")
        if gate == Gate.OK:
            n_ok += 1
        elif frontier_detail is None:
            frontier_detail = (rel, gate, detail)

    print(f"\n{n_ok}/{len(rows)} files pass the front-end gauntlet")
    if frontier_detail is not None:
        rel, gate, detail = frontier_detail
        print(f"\nfirst blocker ({gate}) in {rel}:")
        print(f"  {detail}")
    else:
        print("\nAll target files pass lex+parse+sema. Codegen is the next gate.")
    # Always print every non-OK detail so progress is auditable at a glance.
    if verbose:
        print("\n--- all blockers ---")
        for rel, gate, detail in rows:
            if gate not in (Gate.OK, "MISSING"):
                print(f"[{gate}] {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
