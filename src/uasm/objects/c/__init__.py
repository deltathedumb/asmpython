"""The object runtime as C, in the order a C compiler must read it.

WHY THE ORDER IS THE WHOLE CONTRACT. This was one sixteen-thousand-line
string in one module, and it is still one translation unit: the C has no
forward declarations for most of what it defines, so a function may call
anything ABOVE it and nothing below. `_PARTS` is therefore not a set of
modules that happen to be listed alphabetically -- it is the file, and
reordering it is a compile error rather than a style question.

THE SPLIT FOLLOWS THE AUTHOR'S OWN SECTION BANNERS. Every boundary below
falls on a `/* --- ... --- */` line that was already there, so no part
begins in the middle of anything.
"""
from __future__ import annotations

from . import _core
from . import _bigint
from . import _sequence
from . import _mapping
from . import _exceptions
from . import _inspect
from . import _numeric
from . import _strings
from . import _builtins
from . import _match
from . import _containers
from . import _codecs
from . import _format
from . import _math
from . import _async
from . import _iteration
from . import _classes
from . import _descriptors
from . import _calling

#: The parts, IN SOURCE ORDER. See the module docstring.
_PARTS = (
    _core,        #   855 lines  the preamble, errors, source positions and construction
    _bigint,      #   781 lines  arbitrary-precision integers
    _sequence,    #   827 lines  sequences, and printing
    _mapping,     #   602 lines  dict, set and frozenset
    _exceptions,  #   333 lines  exceptions
    _inspect,     #   722 lines  extraction, inspection, repr and str
    _numeric,     #  1644 lines  the numeric tower, comparison and conversions
    _strings,     #  1079 lines  the string methods
    _builtins,    #  1494 lines  integer methods, and the builtins over sequences
    _match,       #   887 lines  `match`, and PEP 750 template strings
    _containers,  #   470 lines  the list, dict and set methods
    _codecs,      #   491 lines  codecs
    _format,      #   698 lines  the format mini-language
    _math,        #   253 lines  math
    _async,       #  1058 lines  generators, asyncio and tasks
    _iteration,   #   370 lines  the iteration protocol
    _classes,     #   651 lines  callables, cells, function objects, classes and attributes
    _descriptors, #  1220 lines  the descriptor protocol
    _calling,     #  1565 lines  calling, type objects and operator dispatch
)

#: The runtime's C, whole. Everything downstream -- the signature
#: scraper, `split_c`, `_declare_only` -- reads this one string, exactly
#: as it did when it was written out longhand in one module.
OBJECTS_C = "".join(part.C for part in _PARTS)

__all__ = ["OBJECTS_C"]
