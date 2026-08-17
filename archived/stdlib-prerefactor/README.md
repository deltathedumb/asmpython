# The stdlib as it stood before the rebuild

27 modules, 6,807 lines, moved out of
`src/asmpython/frontends/python/bundled/` so the standard library could be
built again from scratch against CPython 3.14 rather than grown outwards from
whatever a conformance case happened to need next.

**They are reference, not history.** Every one of these was written to make
something specific pass, and most of them are partial in ways that are not
written down anywhere -- `typing` has the classes and not the special forms,
`sys` is half compiler constants, `io` covers what `print` needed. When a
module is rebuilt, the version here is worth reading first: it is a record of
which corners of that module a real program actually reached.

## What was NOT archived, and why

`bundled/` still holds six modules:

    _pyast  _pycompile  _pylex  _pyparse  _pyrun  _pyvalidate

Those are not the standard library. They are the Python-in-Python compiler and
tree walker that `bundled.py` splices into any program naming `compile`,
`eval` or `exec` -- 3,295 lines of it -- and nineteen conformance cases depend
on them. They are spliced by the same machinery for the same reason, which is
the only thing they have in common with what is in here.

`ctypes` is not here either, and never was: it is a compile-time feature of
the frontend (`frontends/python/cffi.py`), not a bundled module.

## The test that came with them

`test_unicodedata.py` was `tests/asmpython/unit/test_bundled_unicodedata.py`.
It loads the bundled module BY PATH at import time, so with the module gone it
did not fail -- it crashed COLLECTION, and the whole suite with it. Archived
beside the module it measures, and it comes back when `unicodedata` does.

It is worth reading first when that happens: it compares the bundled module
against the running CPython's `unicodedata` codepoint by codepoint, which is
the shape every module's test now takes (`tests/stdlib/`, see `docs/STDLIB.md`).

## What archiving them costs, measured

Recorded when the rebuild starts, so that each module restored can be measured
against it rather than against a guess. See `docs/STDLIB.md`.

## How to read one

They are ordinary Python, compiled by asmpython, and they may only use what
asmpython accepts -- which is the constraint that makes the whole arrangement
honest. `bundled.py`'s header says it plainly:

> a bundled module is compiled by this compiler, so it may only use what this
> compiler accepts. A construct one of them cannot use is a gap worth closing
> rather than a reason to drop back to C.
