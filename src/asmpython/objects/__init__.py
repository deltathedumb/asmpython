"""The object runtime: what a Python value IS at run time, in three spellings.

WHY THIS IS A PACKAGE AND NOT PART OF `link/`. It lived there, and `link/` is
documented as "artifacts -> program". Nineteen thousand of its twenty thousand
lines were this instead, and the mismatch was not cosmetic: every cross-package
import of `link` except one reached past the linker for the runtime --
`frontends.python.analysis` wanted `signatures()`, `backends.c.emit` wanted the
C, `driver.pipeline` wanted the IR half. A frontend importing the LINKER to ask
what `apy_add` takes is a sentence that should not typecheck, and it did,
because the two things shared a name.

THE THREE SPELLINGS, all of the same semantics, and the reason they must agree:

  * `csource` -- the C, linked into every compiled program. The original and
    the one the others are measured against.
  * `ir` -- the part rewritten in asmpython's own machine subset, so a backend
    gets dynamic Python without defining 229 `apy_*` symbols by hand. See
    `docs/INERT-RUNTIME.md`.
  * `asmpython.ir.objects_host` -- the same runtime backed by real Python
    objects, for the reference interpreter. It stays in `ir/` because it is
    the INTERPRETER's, and mutually recursive with it.

AND THE FLOOR BENEATH THEM. A runtime written in IR still cannot talk to the
machine -- the IR has no I/O -- so `floor` names what a backend must supply
that is not IR, `hostsvc` is the table those names are called through, and
`support` is the C that satisfies them for a hosted target.
"""
from __future__ import annotations

from . import floor, hostsvc, ir, support
from .csource import OBJECT_NAMES, OBJECTS_C, objects_c, signatures, split_c
from .floor import FLOOR
from .support import (
    ENTRY_SYMBOL, RUNTIME_C, needs_runtime, runtime_c, write_runtime,
)

__all__ = [
    "ENTRY_SYMBOL", "FLOOR", "OBJECTS_C", "OBJECT_NAMES", "RUNTIME_C",
    "floor", "hostsvc", "ir", "needs_runtime", "objects_c", "runtime_c",
    "signatures", "split_c", "support", "write_runtime",
]
