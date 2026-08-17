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
| `warnings` | `warn`, filters, `catch_warnings`, `formatwarning`, `deprecated`; NOT the once/default registry, `stacklevel`, or the warning CPython issues when a class SUBCLASSES a deprecated one |
| `types` | `ModuleType`, `SimpleNamespace`, `new_class` |
| `itertools` | `count`, `repeat`, `chain`, `islice`, `groupby`, `product`, `combinations` |
| `functools` | `reduce`, `wraps`, `total_ordering`, `partial`, `cached_property`, `lru_cache`, `cache`, `singledispatch` |
| `re` | the whole ordinary language and surface; NOT lookbehind, conditional/atomic groups, possessive quantifiers, `\N{...}`, property escapes, `Scanner`, `bytes` patterns -- each refused BY NAME |
| `contextlib` | `contextmanager`, `suppress`, `ExitStack`, `nullcontext`; NOT the `async` half, `closing`, `redirect_*`, `chdir` |

**Restoring is not free, and that is the point of stating coverage.** Three of
`itertools`'s seven functions were wrong in ways the old suite never asked
about, and each was a WRONG ANSWER rather than a missing one:

* `islice` accepted a step and ignored it -- `islice(xs, 1, 6, 2)` returned
  every element instead of every second
* `product` had no `repeat=`, the usual spelling of a fixed-width product
* `groupby` named its local `key`, shadowing the parameter, so passing a key
  function was accepted and dropped

None would fail a test that only asserted what the implementation already did.

## What writing one module found in the compiler

`warnings.deprecated` is one decorator. Writing it needed four compiler fixes,
and every one of them was a fault in code that had nothing to do with
`warnings` -- which is the argument for writing the library in the language
rather than in C, stated as a measurement rather than as a preference.

**A positional-only parameter was not visible to a nested function.** The
closure scope builder bound `args.args` and the two star-parameters, so a
parameter in `posonlyargs` or `kwonlyargs` was never bound in its own scope.
`def f(arg, /)` with a closure over `arg` reported `E0052: call to unknown
function 'arg'` and listed every function in the program except the one three
lines above. CPython writes `deprecated.__call__(self, arg, /)` deliberately,
so the module could not be written at all. See `analysis.parameter_names`.

**An exception class held in a VARIABLE could not be constructed on the
compiled path.** The interpreter grew that case and the C did not -- the C's
test read correctly and never fired, because `apy_exc_type` hands its
exception cell to `apy_type_of` and what a program holds for `ValueError` is
a plain type object. So `c = ValueError; c("v")` answered `ValueError() takes
no arguments` under the C backend and worked under the interpreter, for the
same source. `warnings.warn` does exactly this in `raise category(message)`.

**And a class the program wrote by subclassing an exception was worse, on
BOTH paths**: `a = AppError; a(7)` built an ordinary instance, so `str(e)`
read `<AppError object at 0x...>` and `raise e` said `exceptions must derive
from BaseException, not 'AppError'`. Written out as `AppError(7)` it had
always worked, because the frontend resolves that spelling at the call site
-- which is what kept it hidden.

**And the compiler contradicted the coverage line.** Stating what a module
covers is worth nothing if reaching past it reports something else, and it
reported one of two wrong things. `from warnings import deprecated` said
`E0083: no module named 'warnings' is available; there is no import path` --
flatly false, since the module is bundled and every other name in it worked.
`import warnings; warnings.deprecated(...)` said NOTHING at compile time and
raised `NameError: name 'warnings' is not defined` at run time. One sent the
reader after a missing module and the other after a broken import; the truth
in both cases was that the module is here and this member is not.

Both now report `E0084: module 'warnings' has no member 'deprecated'`, from
the splice -- the only pass that can, because afterwards the module is gone
and no module object is left for analysis to have an opinion about. The help
line lists what the module DOES provide, which is the coverage line restated
where someone has just run into its edge. See `bundled._no_member`.

The second and third are the divergence this project is arranged to catch:
two runtimes agreeing about the language and disagreeing about which object a
name holds. They were found by a library module and not by the conformance
suite, because the suite writes exception names out and library code takes
them as arguments.

## `re`, the keystone

Seven of the planned modules import it and three of the four things blocking
self-hosting are it. It landed with the tier-one subset complete: literals,
`.`, `[...]` with ranges and negation, every quantifier greedy and non-greedy
including `{m,n}`, alternation, capturing / non-capturing / named groups,
backreferences by number and name, lookahead both ways, `^ $ \b \B \A \Z`, the
character and category escapes, inline flags both global `(?i)` and scoped
`(?i:...)`, the flags `I M S X A U L`, and the surface `compile search match
fullmatch findall finditer sub subn split escape purge error` with `Match` and
`Pattern`.

**The implementation is a parse tree and a backtracking matcher**, not a
translation of CPython's `sre` bytecode. The bytecode form is faster and is
the wrong thing to copy here: it is an optimisation of an interpreter this
project does not have, and reading it back tells nobody what the module
means. Every node answers `match(ctx, pos, cont)` -- where the WHOLE pattern
ended, or -1 -- and that one decision is why greedy and non-greedy repetition
are four lines apart rather than two algorithms.

**Repetition has two implementations of one meaning**, and the second is not
an optimisation for its own sake. A repetition of something one character wide
that captures nothing -- `.*`, `\d+`, `[a-z]{2,4}`, which is most of them --
is scanned in a loop and backtracked by arithmetic. The general form recurses
once per repetition, so `.*` over a long subject would be as deep as the
subject is long, and this runtime's stack is not.

**What it refuses, it refuses BY NAME**: lookbehind, conditional groups,
atomic groups, possessive quantifiers, `\N{...}`, property escapes. An engine
that quietly matches the wrong thing is worse than one that says it cannot --
`(?<=a)b` read as an ordinary group matches a different language and reports
nothing. Those refusals are the one thing `tests/stdlib/re.py` structurally
cannot check, because CPython HAS those features and a differential test of
them could only ever fail; they are measured against asmpython alone in
`test_stdlib.py::test_re_refuses_what_it_does_not_have`.

**Still outstanding: `warnings.filterwarnings` does not use it yet.** `_match`
there is a prefix test standing in for a regular expression, and says so. The
reason it stays is a dependency and not an oversight: `_pycompile` imports
`warnings` for one `SyntaxWarning`, so pointing `warnings` at `re` would
splice the whole engine into every program that calls `compile`, `eval` or
`exec`. The fix is for `_pycompile` to stop needing all of `warnings`, which
is its own piece of work.

## Held back, and released

`contextlib` was withheld: its `__exit__` called `gen.throw` correctly and the
cleanup still did not run when the block raised, while the same generator
throwing into the same `finally` worked in isolation. A context manager whose
cleanup silently does not run is worse than an absent one, so it stayed out
until the fault was understood rather than until it happened to pass.

**It was never a fault in this module.** Taking the shape apart one layer at a
time settled it -- a generator resumed past its last `yield`, then resumed
from a function, then from a method, then through the `with` protocol, then
through a factory, then thrown into with a `try` around the yield, then with a
`finally`. The ladder is written down in
`scratchpad/ctx-probe.py`-shaped form and every rung already agreed with
CPython, and so did the module itself once the compiler fixes above landed.

That is worth stating plainly: **withholding it was right and the diagnosis
was wrong.** The module was suspected because it was the new thing, and the
fault was in the runtime the whole time -- which is what a ladder of
one-difference-at-a-time cases is for, and what "a failure is asmpython's
until shown otherwise" means applied to this suite's own output.

Nothing is held back now.

## What it cost to start

Recorded so each module restored is measured against a real baseline rather
than an impression. Filled in as the rebuild proceeds.

| | conformance (spec+cpython) | suite |
| --- | --- | --- |
| before archiving | 1668/1668 | 29,559 |
| after archiving | *pending* | *pending* |
