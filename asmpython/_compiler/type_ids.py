"""Runtime type and exception IDs, shared by every stage.

These are part of the RUNTIME CONTRACT, not of any one backend: the tag a
boxed value carries, the id `type()`/`isinstance()` compare against, and the
exception hierarchy `except` walks. ir_lower, sema and each backend all have
to agree on them.

They used to live in `_compiler/codegen.py`, the legacy NASM backend, so
`ir_lower` -- the modern IR pipeline -- imported the legacy backend purely to
read six constants. That is backwards, and it is one of the threads tying the
legacy backend into the build. Splitting them out is a prerequisite for
archiving it: see ROADMAP. codegen.py re-exports them, so nothing that
imported them from there had to change.
"""

from __future__ import annotations


# Wildcard exception-type id for "untyped" raises (`raise "a string"`, or
# raising a `str` value) -- matches *any* `except` clause, typed or not. This
# preserves the historical "catches anything" behaviour for code that raises
# bare strings while still allowing `except SomeType:` to correctly reject
# exceptions of other concrete types.
EXC_ANY = 0

# Builtin exception types and their CPython parent (for `except LookupError:`
# to also catch a raised `KeyError`, etc.). `None` marks the hierarchy root.
BUILTIN_EXC_PARENTS: dict[str, str | None] = {
    "BaseException": None,
    "Exception": "BaseException",
    "SystemExit": "BaseException",
    "KeyboardInterrupt": "BaseException",
    "ArithmeticError": "Exception",
    "ZeroDivisionError": "ArithmeticError",
    "OverflowError": "ArithmeticError",
    "LookupError": "Exception",
    "IndexError": "LookupError",
    "KeyError": "LookupError",
    "NameError": "Exception",
    "AttributeError": "Exception",
    "TypeError": "Exception",
    "ValueError": "Exception",
    "RuntimeError": "Exception",
    "NotImplementedError": "RuntimeError",
    "AssertionError": "Exception",
    "ImportError": "Exception",
    "OSError": "Exception",
    "FileNotFoundError": "OSError",
    "StopIteration": "Exception",
}
# Fixed ids 1..N in the same order as BUILTIN_EXC_PARENTS (0 reserved for EXC_ANY).
# Written as a literal so _merge_import_bindings can materialize it as a global.
BUILTIN_EXC_IDS: dict[str, int] = {
    "BaseException": 1,
    "Exception": 2,
    "SystemExit": 3,
    "KeyboardInterrupt": 4,
    "ArithmeticError": 5,
    "ZeroDivisionError": 6,
    "OverflowError": 7,
    "LookupError": 8,
    "IndexError": 9,
    "KeyError": 10,
    "NameError": 11,
    "AttributeError": 12,
    "TypeError": 13,
    "ValueError": 14,
    "RuntimeError": 15,
    "NotImplementedError": 16,
    "AssertionError": 17,
    "ImportError": 18,
    "OSError": 19,
    "FileNotFoundError": 20,
    "StopIteration": 21,
    "IOError": 19,  # alias for OSError (same id)
}

# A bare builtin scalar/container type name used as a value (`{"type": str}`,
# mimicking argparse's `type=str`) -- same RTTI-id trick as class_ids /
# BUILTIN_EXC_IDS. Negative so it can never collide with class_ids (which
# starts at 0).
#
# Historically these ids were a compile-time-only placeholder the program
# never inspected at runtime. They are now ALSO the runtime type tag written
# into a boxed opaque cell's "__class__" key whenever a scalar value (int,
# float, bool, str) crosses into a genuinely unknown ("any") static type --
# see ir_lower.py's `_lower_box_any`/`_lower_read_any_tag`. type()/
# isinstance() on an "any"-typed operand read this same tag at runtime
# (mirroring how a user instance's own "__class__" tag already works),
# instead of guessing from the operand's (nonexistent, for "any") static
# type. NONE_TYPE_ID has no bare-name entry above `type X` syntax can
# produce (`None` isn't a type name), so it's kept as a separate constant
# rather than a BUILTIN_TYPE_IDS member.
BUILTIN_TYPE_IDS: dict[str, int] = {
    "int": -1,
    "float": -2,
    "str": -3,
    "bool": -4,
    "list": -5,
    "dict": -6,
    "tuple": -7,
    "set": -8,
    # The three builtin descriptor wrappers. `staticmethod(f)` /
    # `classmethod(f)` / `property(fget)` construct a tagged cell (same
    # instance-shaped "__class__"-keyed mechanism as a boxed scalar or a
    # user instance) carrying the wrapped function; isinstance() reads this
    # tag, and `.__func__` / `.fget` read the payload back. asmpython has no
    # real descriptor protocol of its own -- these exist so a Python VM
    # authored in the subset (portapy) can use them as the plain markers
    # they are (`isinstance(v, staticmethod)`, `v.__func__`), with all
    # actual get/dispatch semantics living in that VM's own code.
    "staticmethod": -10,
    "classmethod": -11,
    "property": -12,
    # `slice(start, stop, step)` -- a runtime slice object (the dynamic form
    # of `x[a:b:c]`, e.g. a bytecode VM's BUILD_SLICE opcode). Same tagged-
    # cell mechanism: "__class__" holds this id, and "start"/"stop"/"step"
    # hold the bounds. A dynamic `obj[slice_obj]` subscript detects the tag
    # and dispatches to the runtime slice helper (see ir_lower's
    # _lower_slice_ctor / the any-subscript slice branch).
    "slice": -13,
}
NONE_TYPE_ID = -9
# Sentinel returned by the runtime tag read when a pointer carries no
# recognizable "__class__" tag at all (an ordinary, never-boxed dict/list/
# tuple/instance-shaped value, or a value that predates this feature).
# Disjoint from every real id above and from every real class id (>= 0).
UNTAGGED_ID = -1000
