# The standard library, rebuilt

**Target: CPython 3.14, module by module, each one measured against it.**

The previous set is in `archived/stdlib-prerefactor/` -- 27 modules, 6,807
lines. It was not wrong, it was UNPLANNED: each module was written to the depth
some conformance case happened to need, so `typing` has the classes and not the
special forms, `sys` is half compiler constants, and nothing records which half
of anything is there. A library nobody can predict the coverage of is one every
user has to test for themselves.

This time the coverage is the deliverable, and it is stated per module.

## How a module is built

**It is ordinary Python, compiled by asmpython.** That is the constraint that
makes the whole arrangement honest, and `bundled.py` has said so from the
start:

> a bundled module is compiled by this compiler, so it may only use what this
> compiler accepts. A construct one of them cannot use is a gap worth closing
> rather than a reason to drop back to C.

So a module that cannot be written is a compiler bug with a name, not a reason
to write C. That has already paid: the docstring gap, the width rules and the
static-path module storage were all found by writing library code.

**Nothing is loaded at run time.** `import functools` splices that module's
definitions into the program under mangled names -- see `bundled.py`. There is
no module object, no import system, and no cost to a program that imports
nothing.

## How a module is proved

`tests/stdlib/<module>.py` is a program that exercises it. The runner executes
it under **CPython** and under **asmpython** and compares the output exactly.

That is the corpus argument applied to the library: CPython is the oracle, the
test is written against the SPECIFICATION rather than against what asmpython
currently does, and a divergence is asmpython's until proven otherwise. A test
that only asserts what already works tests nothing.

Each test file opens with a coverage line saying what of the module it claims.
That line is the module's contract and the thing this rebuild exists to make
true.

## The order, and why

Dependency order first, value second. A module may only import ones already
built.

    0  keyword  operator  types  abc  sys            no dependencies at all
    1  collections.abc  functools  itertools  enum   the protocol furniture
       numbers
    2  collections  dataclasses  typing  contextlib  what ordinary code uses
       copy  string  bisect  heapq  struct
    3  io  os  pathlib  json  re  textwrap           the ones with real
       traceback  warnings  inspect                  surface
    4  math  decimal  fractions  statistics          numeric and temporal
       random  datetime  zoneinfo
    5  time  socket  threading  subprocess  select   NEEDS THE FLOOR TO GROW

**Tier 5 is a different kind of work and is not scheduled here.** Those modules
need real syscalls, and the platform floor is deliberately three functions
(`docs/INERT-RUNTIME.md`). Each one is a decision about that floor rather than
a porting job -- and `ctypes` has just changed the arithmetic, because a C
library can now be called with a declared signature and no new platform
function at all. `time.time()` through `ctypes` is worth trying before
`time` is written by hand.

## What is not the standard library

`bundled/` also holds `_pyast`, `_pycompile`, `_pylex`, `_pyparse`, `_pyrun`
and `_pyvalidate`. Those are the Python-in-Python compiler spliced into any
program naming `compile`, `eval` or `exec`. They are spliced by the same
machinery and that is all they have in common with a library module; they were
not archived and are not rebuilt.

`ctypes` is a compile-time feature of the frontend (`frontends/python/cffi.py`)
rather than a bundled module, and is unaffected.

## Rebuilt so far

| module | coverage |
| --- | --- |
| `keyword` | complete |
| `warnings` | `warn`, filters, `catch_warnings`, `formatwarning`; NOT the once/default registry, `stacklevel`, or `deprecated` |
| `types` | `ModuleType`, `SimpleNamespace`, `new_class` |
| `itertools` | `count`, `repeat`, `chain`, `islice`, `groupby`, `product`, `combinations` |
| `functools` | `reduce`, `wraps`, `total_ordering`, `partial`, `cached_property`, `lru_cache`, `cache`, `singledispatch` |

**Restoring is not free, and that is the point of stating coverage.** Three of
`itertools`'s seven functions were wrong in ways the old suite never asked
about, and each was a WRONG ANSWER rather than a missing one:

* `islice` accepted a step and ignored it -- `islice(xs, 1, 6, 2)` returned
  every element instead of every second
* `product` had no `repeat=`, the usual spelling of a fixed-width product
* `groupby` named its local `key`, shadowing the parameter, so passing a key
  function was accepted and dropped

None would fail a test that only asserted what the implementation already did.

## Held back

`contextlib` is written and NOT bundled. Its `__exit__` calls `gen.throw`
correctly and the cleanup still does not run when the block raises, while the
same generator throwing into the same `finally` works in isolation -- so the
fault is in the interaction and is not yet found. A context manager whose
cleanup silently does not run is worse than an absent one, so it stays out
until it is understood.

## What it cost to start

Recorded so each module restored is measured against a real baseline rather
than an impression. Filled in as the rebuild proceeds.

| | conformance (spec+cpython) | suite |
| --- | --- | --- |
| before archiving | 1668/1668 | 29,559 |
| after archiving | *pending* | *pending* |
