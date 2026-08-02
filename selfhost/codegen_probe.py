"""Codegen probe: run the FULL pipeline (lex -> parse -> sema -> codegen .asm)
over the compiler's own source and report the first codegen failure per file.

The front-end gauntlet (`selfhost.check`) only measures lex/parse/sema. This
probe goes one step further and actually invokes codegen, which is the larger,
unmeasured half of self-compilation. Codegen raises ordinary Python exceptions
(NotImplementedError, KeyError, NameError, ...) rather than CompileError, so we
catch broadly and surface where in codegen it blew up.

Usage:
    python -m selfhost.codegen_probe            # summary + first codegen blocker per file
    python -m selfhost.codegen_probe --verbose  # full tracebacks
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asmpython._compiler.program import load_program  # noqa: E402
from asmpython._compiler.sema import analyze as sema_analyze  # noqa: E402
from asmpython._targets.target_windows import WindowsCodegen  # noqa: E402
from asmpython._compiler.errors import CompileError  # noqa: E402

from selfhost.check import TARGETS  # noqa: E402


def probe_file(path: Path, *, verbose: bool) -> tuple[str, str]:
    """Return (status, detail). status == 'OK' means codegen produced .asm.

    Uses whole-program loading (the real self-compile path) so cross-module
    classes/functions from sibling files are merged in before sema/codegen.
    """
    src = path.read_text(encoding="utf-8")
    try:
        module = load_program(src, path)
    except CompileError as e:
        return "FRONTEND", f"parse/lex: {e}"

    try:
        sema_analyze(module, source_dir=path.parent)
    except CompileError as e:
        return "FRONTEND", f"sema: {e}"
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc() if verbose else ""
        return "FRONTEND", f"sema crash: {e!r}\n{tb}"

    try:
        gen = WindowsCodegen(module)
        gen.generate()
    except Exception as e:  # noqa: BLE001
        # Find the deepest frame inside codegen for a useful pointer.
        tb_lines = traceback.extract_tb(sys.exc_info()[2])
        loc = ""
        for fr in reversed(tb_lines):
            if "codegen.py" in fr.filename or "target_" in fr.filename:
                loc = f" [{Path(fr.filename).name}:{fr.lineno} {fr.name}]"
                break
        detail = f"{type(e).__name__}: {e}{loc}"
        if verbose:
            detail += "\n" + traceback.format_exc()
        return "CODEGEN", detail

    return "OK", ""


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv
    rows: list[tuple[str, str, str]] = []
    for rel in TARGETS:
        path = ROOT / rel
        if not path.exists():
            rows.append((rel, "MISSING", ""))
            continue
        status, detail = probe_file(path, verbose=verbose)
        rows.append((rel, status, detail))

    name_w = max(len(r[0]) for r in rows)
    print("codegen probe (lex -> parse -> sema -> codegen .asm)\n")
    n_ok = 0
    first_blocker = None
    for rel, status, detail in rows:
        mark = {
            "OK": "OK  ",
            "CODEGEN": "CGEN",
            "FRONTEND": "FE  ",
            "MISSING": "MISS",
        }.get(status, "????")
        print(f"  [{mark}] {rel.ljust(name_w)}  {status}")
        if status == "OK":
            n_ok += 1
        elif first_blocker is None and status == "CODEGEN":
            first_blocker = (rel, detail)

    print(f"\n{n_ok}/{len(rows)} files compile through codegen")
    if first_blocker is not None:
        rel, detail = first_blocker
        print(f"\nfirst CODEGEN blocker in {rel}:\n  {detail}")
    if verbose:
        print("\n--- all codegen/front-end blockers ---")
        for rel, status, detail in rows:
            if status in ("CODEGEN", "FRONTEND"):
                print(f"[{status}] {rel}\n  {detail}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
