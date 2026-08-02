"""The runtime the Python frontend's programs link against.

The IR has no I/O opcodes -- `print` is a call to a named function like any
other, and something must define it. That something is here rather than in a
backend, because every backend needs the same one and a backend that carried
its own copy would drift from the others exactly as the two liveness analyses
in the tree this replaces did.

It is C, and it is deliberately tiny. A frontend that wants a different
runtime supplies its own; nothing in the IR, the backends or the linker knows
what these functions do.

FLOAT FORMATTING is `%f`, matching `Interpreter._host`. Python's repr would
print `32.0` where this prints `32.000000`. The divergence is real and
documented in docs/LANGUAGE.md; what matters is that the interpreter and every
compiled binary agree, because a reference implementation that disagrees with
the thing it is a reference for is worse than none.
"""
from __future__ import annotations

from pathlib import Path

#: Definitions for the host functions `frontends/python` emits calls to, plus
#: the C entry point that calls the IR's `main`.
#:
#: The IR's `main` is NOT C's `main`: it returns i64 and C requires int, and on
#: Windows the real entry point is further wrapped again. The backend renames
#: it and this calls it, so the rename lives in one place.
RUNTIME_C = """\
/* asmpython runtime for the Python frontend. Generated -- edit link/runtime.py. */
#include <stdio.h>
#include <stdint.h>

void print_int(int64_t v)   { printf("%lld\\n", (long long)v); }
void print_float(double v)  { printf("%f\\n", v); }
void print_str(const char *s) { fputs(s, stdout); }

extern int64_t @ENTRY@(void);

int main(void) { return (int)@ENTRY@(); }
"""

#: Substituted by `write_runtime`. A literal token rather than %-formatting or
#: str.format: this text is C, it is full of `%lld` and `%f`, and both of those
#: mechanisms would try to interpret them. `%(entry)s` here raised
#: "unsupported format character 'l'" on the very first link.
_ENTRY_TOKEN = "@ENTRY@"

# The name the backend gives the IR's `main`. Imported, not repeated: the
# backend writing the symbol and this runtime calling it must agree.
from ..backend.base import ENTRY_SYMBOL  # noqa: E402


def write_runtime(directory: Path, *, entry: str = ENTRY_SYMBOL) -> Path:
    """Write the runtime C file into `directory` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "asmpython_runtime.c"
    path.write_text(RUNTIME_C.replace(_ENTRY_TOKEN, entry), encoding="utf-8")
    return path


def needs_runtime(module) -> bool:
    """Whether `module` calls anything the runtime provides.

    A module that never prints does not need it linked in, and linking an
    unused object is how a "no dependencies" claim quietly stops being true.
    """
    from ..ir.opcodes import Op
    provided = {"print_int", "print_float", "print_str"}
    return any(ins.sym in provided
               for f in module.functions for b in f.blocks
               for ins in b.instructions if ins.op is Op.CALL)
