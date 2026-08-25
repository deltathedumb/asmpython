"""`apir` -- ASMPython Intermediate Representation, as a container.

NOT WRITTEN YET, but no longer unidentified. This file used to say the format
could not be pinned down, because APIR was named alongside nine PUBLISHED
formats and nothing external matches it. It is not external: **APIR is
asmpython's own IR**, serialised, and the pre-rewrite compiler shipped a
working implementation of it -- `archived/legacy/asmpython/_compiler/ssa/
irfreeze.py`, whose container is the specification this backend has to meet.

THE CONTAINER, read off that file rather than invented:

    struct.Struct("<5sHBBII32s")
      5s   b"APIR\\x00"           magic
      H    format version        1
      B    codec                 1 = marshal
      B    flags                 0
      I    metadata length
      I    payload length
      32s  sha256(payload)       integrity, checked on load
    then   metadata (JSON), then payload

`.apir.json` is the same object graph as `{"format": "asmpython-ir",
"format_version", "metadata", "ir"}` -- for reading, not for loading. Writes
go to a `.tmp` and are renamed, so a killed build never leaves a half-written
container that passes its own magic check.

A BINARY BACKEND, and this file said `language` until the format was
identified. `.apir` is bytes with a checksum; the JSON sibling is an
inspection format beside it, the way a `.jar` has a readable manifest. The
guess was wrong in the direction that matters -- it would have licensed
emitting text as the artifact.

WHAT THE METADATA IS FOR, and it is the reason this is worth having at all:
`source_sha256`, the pass list, and a hash PER FUNCTION. The legacy
`fastcomp.py` used exactly that to skip recompiling functions whose hash had
not moved. So this is the incremental-build format, not just a dump -- which
is why `--emit-ir` does not already cover it.

MARSHAL IS THE ONE THING NOT TO COPY. It is CPython-version-specific and will
execute what it is given, so a `.apir` from another interpreter is either
unreadable or a security problem. The rewrite has its own printer and parser
for the IR (`ir/printer.py`), so the payload should be that -- a format this
project defines and can read on any interpreter -- rather than a pickle of
whatever objects happened to be in memory.

`ready = False`, so the driver warns before `emit` is ever reached.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module
from .alib import ALIB


class ApirBackend(Backend):
    name = "apir"
    description = "ASMPython IR, as a versioned integrity-checked container"
    #: Bytes with a checksum. See the note above on why this said "language".
    kind = "binary"
    ready = False
    #: A PLACEHOLDER. APIR describes no machine -- it is the IR itself -- so
    #: its "target" is whichever platform the container is later built FOR,
    #: and that is a property of the second compilation rather than of this
    #: one. `c` stands in until that is designed.
    default_target = "c"
    alib = ALIB

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the apir backend is not written yet. What it needs: a writer for "
            "the container in archived/legacy/asmpython/_compiler/ssa/"
            "irfreeze.py, with the marshal payload replaced by this tree's "
            "own IR text (ir/printer.py), plus the per-function hashes the "
            "incremental path reads")


register(ApirBackend())
