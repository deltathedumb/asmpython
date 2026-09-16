"""`uir` -- a Universal Intermediate Representation, as a container.

THE LETTERS CHANGED THIS TIME, not just their expansion. The format's first
rename kept the same four bytes and only reworded what they stood for --
"ASMPython Intermediate Representation" becoming "A Portable Intermediate
Representation" while APIR stayed APIR. This rename moved the project too,
from `asmpython` to `uasm`, and the acronym moved with it: UIR, a Universal
Intermediate Representation, and nothing in the name mentions the frontend
that happens to be first.

NOT WRITTEN YET, but not unidentified. UIR is this project's own IR,
serialised, and the pre-rewrite compiler shipped a working implementation of
its predecessor format -- `archived/legacy/asmpython/_compiler/ssa/irfreeze.py`,
whose container is still the specification this backend has to meet.

TWO SPELLINGS, ONE FORMAT. `.uir` is the text `ir/printer.py` writes and
parses; `.uirb` is the container below. The pair reads as `.wat` to `.wasm`
does -- the shorthand for the form you read, the longer one for the artifact
you ship -- and the container's payload IS the text, so the two are the same
IR at two encodings rather than two formats.

THE CONTAINER, read off that file rather than invented:

    struct.Struct("<5sHBBII32s")
      5s   b"UIR\\x00\\x00"        magic
      H    format version        1
      B    codec                 1 = marshal
      B    flags                 0
      I    metadata length
      I    payload length
      32s  sha256(payload)       integrity, checked on load
    then   metadata (JSON), then payload

`.uirb.json` is the same object graph as `{"format": "uir",
"format_version", "metadata", "ir"}` -- for reading, not for loading. Writes
go to a `.tmp` and are renamed, so a killed build never leaves a half-written
container that passes its own magic check.

A BINARY BACKEND, and this file said `language` until the format was
identified. `.uirb` is bytes with a checksum; the JSON sibling is an
inspection format beside it, the way a `.jar` has a readable manifest. The
guess was wrong in the direction that matters -- it would have licensed
emitting text as the artifact.

WHAT THE METADATA IS FOR, and it is the reason this is worth having at all:
`source_sha256`, the pass list, and a hash PER FUNCTION. The legacy
`fastcomp.py` used exactly that to skip recompiling functions whose hash had
not moved. So this is the incremental-build format, not just a dump -- which
is why `--emit-ir` does not already cover it.

MARSHAL IS THE ONE THING NOT TO COPY. It is CPython-version-specific and will
execute what it is given, so a `.uirb` from another interpreter is either
unreadable or a security problem. The rewrite has its own printer and parser
for the IR (`ir/printer.py`), so the payload should be that `.uir` text -- a
format this project defines and can read on any interpreter -- rather than a
pickle of whatever objects happened to be in memory.

`ready = False`, so the driver warns before `emit` is ever reached.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module


class UirBackend(Backend):
    name = "uir"
    description = "UIR containers (.uirb): the IR itself, versioned and integrity-checked"
    #: Bytes with a checksum. See the note above on why this said "language".
    kind = "binary"
    ready = False
    #: A PLACEHOLDER. UIR describes no machine -- it is the IR itself -- so
    #: its "target" is whichever platform the container is later built FOR,
    #: and that is a property of the second compilation rather than of this
    #: one. `c` stands in until that is designed.
    default_target = "c"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the uir backend is not written yet. What it needs: a writer "
            "for the .uirb container in archived/legacy/asmpython/_compiler/"
            "ssa/irfreeze.py, with the marshal payload replaced by this "
            "tree's own .uir text (ir/printer.py), plus the per-function "
            "hashes the incremental path reads")


register(UirBackend())
