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
| `inspect` | `signature`, `Signature`, `Parameter` and the five kinds, `isfunction`, `isclass`, `getdoc` of a FUNCTION; NOT source, frames, `bind`, `getfullargspec` |
| `__future__` | complete |
| `enum` | `Enum`, `IntEnum`, `StrEnum`, `Flag`, `IntFlag`, `auto`, `unique`, aliases, `__members__`; NOT `verify`/`EnumCheck`, `boundary=`, `global_enum`, `member`/`nonmember`, `_missing_`, functional creation |
| `copy` | `copy`, `deepcopy`, the hooks, the memo; NOT `__reduce__`, `__getstate__`, `copyreg`, `copy.replace` |
| `dataclasses` | `dataclass` with init/repr/eq/order/unsafe_hash/frozen/match_args/kw_only, `field` with all eight arguments, `Field`, `fields`, `is_dataclass`, `asdict`/`astuple` recursing, `replace`, `make_dataclass`, `InitVar`, `KW_ONLY`, `MISSING`, `FrozenInstanceError`, `__post_init__`, ClassVar exclusion, inheritance; NOT `slots`, `weakref_slot` (refused BY NAME), `field(doc=)`, `Field[int]` |
| `pathlib` | the whole PURE half -- `PurePosixPath`, `parts`, `name`, `stem`, `suffix`, `suffixes`, `parent`, `parents`, `root`, `anchor`, `is_absolute`, `joinpath`, `with_name`, `with_suffix`, `with_stem`, `relative_to`, `match`, `as_posix`, `/`, comparison, `__fspath__`; and a CONCRETE `Path` with `exists`, `is_file`, `is_dir`, `read_bytes`, `read_text`, `write_bytes`, `write_text`, `mkdir`, `touch`, `unlink`, `rmdir`. NOT `iterdir`, `glob`, `rglob`, `stat`, `resolve`, `absolute`, `open`, symlinks -- each refused BY NAME -- and `Path` is POSIX-flavoured, so `str()` renders `/` where CPython on Windows renders `\`. Windows-only concrete half. |

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

## `pathlib` is not a module-writing problem, and this is what it is

The self-host probe ranks `pathlib` first at **30 of 79 files**, which reads
like the next module to write. It is not, and the reason is worth having
written down before someone spends a day on it.

**The compiler's own source needs the CONCRETE half.** Counted, not guessed:
`resolve` 14, `mkdir` 11, `write_text` 8, `read_text` 8, `exists` 8,
`absolute` 7, `glob` 5, `write_bytes` 4, `is_file`/`is_dir`/`rglob`/
`read_bytes` the rest. The archived module is 168 lines of the PURE half --
`PurePosixPath` and nothing that touches a disk -- and it is correct as far as
it goes.

**Shipping the pure half alone would LOWER THE PROBE'S NUMBER AND FIX
NOTHING.** Add a `Path = PurePosixPath` alias and those 30 files import
cleanly and compile; they then fail at run time on the first `read_text`. That
is precisely the "accepted and ignored" shape this rebuild exists to prevent
-- `frozen=True` in the archived `dataclasses`, `islice`'s ignored step -- and
it would be worse here because a green probe would say the constraint had
moved when it had not.

**`ctypes` LOOKED like the way in and is not, yet.** `docs/STDLIB.md` already
noted that a C library can be called with no new platform function, so
`fopen`/`fread`/`stat` through `ctypes` would give a concrete `Path` without
growing the floor. Measured instead of assumed:

    def static_call(x: f64) -> f64:
        return libm.sqrt(x)          # works, prints 3.0

    def dynamic_call(x):
        return libm.sqrt(x)          # NameError: name 'libm' is not defined

**`ctypes` resolves at COMPILE time on the STATICALLY TYPED path only.** A
bundled module is ordinary dynamic Python, so it cannot reach a C library at
all -- and `pathlib` has to be a bundled module. The same is true of any
module that would need the filesystem.

**THE FIRST HALF OF THAT IS NOW DONE.** `ctypes` is reachable from dynamic
code: `_dyn_call` recognises the shape and `dynamic._dyn_ctypes_call` emits the
same single `Op.CALL` the static path does, with an unbox before each argument
and a box after the result. The cause was narrow -- `ctypes_calls` was recorded
only by the static analyser and read only by the static lowerer, so a dynamic
call lowered as an ordinary attribute access on a name the splice had removed.

**POINTERS WORK NOW TOO.** `apy_str_bytes` hands a native call the bytes of a
`str` or `bytes`, checking the kind at run time because a dynamic value's kind
is a run-time fact -- handing a native function the address of an integer cell
is how a ctypes program corrupts memory rather than failing.

Both safety questions were CHECKED rather than assumed. NUL TERMINATION: every
producer writes one, including a slice, which reaches `apy_str_copy` like
everything else -- and the remaining C already depends on it in two hundred
places through `APY_CSTR`. LIFETIME: a literal lives in read-only program data
and an arena string is immortal, so nothing a callee keeps can be freed
underneath it. The bump-pointer arena that cannot free is exactly what makes
that answerable.

**A REAL FILESYSTEM QUERY NOW WORKS FROM DYNAMIC PYTHON**, byte-identical to
CPython:

    k32 = ctypes.CDLL("kernel32")
    k32.GetFileAttributesA.restype = ctypes.c_uint32
    k32.GetFileAttributesA.argtypes = [ctypes.c_char_p]

    def exists(path):
        return k32.GetFileAttributesA(path) != 4294967295

**BUT NOT THROUGH LIBC, AND THAT IS THE NEXT OBSTACLE.** `objects.py` includes
`<stdio.h>`, `<stdlib.h>`, `<string.h>`, `<math.h>` and `<errno.h>`, and the
backend emits `uint64_t strlen(uintptr_t)` for a symbol the IR declares --
which gcc rejects against `size_t strlen(const char *)`. Scalars are fine
(`double sqrt(double)` matches); pointers are not, because the IR has one
pointer type and C has many. So `sqrt` works and `fopen`, `strlen` and
`getenv` do not, and that is PRE-EXISTING -- the static path fails the same
way, checked.

So `pathlib` is written against the platform's own API rather than libc, or
the backend learns to CALL THROUGH A CAST instead of declaring an extern --
a change to `backends/c/emit.py`. The first works today; the second is the
general fix.

### It is written, and this is what it took

**THE FIRST ROUTE WAS TAKEN.** `bundled/pathlib.py` is the pure half restored
plus a concrete `Path`, and it passes `tests/stdlib/pathlib.py` against
CPython on the interpreter and compiled alike. The symbols are chosen for the
obstacle above rather than for tidiness: `_open`, `_read`, `_write`, `_close`
and `_lseek` from the C runtime, because `<fcntl.h>` and `<io.h>` are NOT
included and so nothing conflicts with them; and `GetFileAttributesA`,
`CreateDirectoryA`, `DeleteFileA` and `RemoveDirectoryA` from kernel32,
because `_unlink` and `_mkdir` ARE declared in MinGW's `<stdio.h>` and hit
exactly the conflict described above. `GetFileAttributesA` also answers
`is_dir` outright, where libc offers nothing short of `stat` and a struct
this frontend cannot receive. The general fix in `emit.py` is still worth
making; it is no longer what stands between here and a filesystem.

**READING NEEDED A WRITABLE BUFFER AND `bytearray` IS ONE.** Its cell is a
string's cell with the mutable flag set, so `apy_str_bytes` hands over the
object's own storage and `_read` fills it in place. Nothing new was added to
the object model to get it.

**A NULL POINTER ARGUMENT HAD TO BE ALLOWED.** `CreateDirectoryA(path, 0)` is
the ordinary spelling of "no security descriptor"; `apy_str_bytes` refused an
int, which made every native call with a NULL argument a TypeError. C's own
rule for a pointer parameter admits a null pointer constant, and now so does
this.

**THE INTERPRETER HAD TO LEARN THE SYMBOLS, and that is the part with real
weight.** A `ctypes` declaration is resolved by the LINKER, and the IR
interpreter has none -- so every concrete method trapped there with `call to
undefined function '_open'`. The interpreter is the ORACLE the C backend is
measured against, so a module it cannot execute is a module whose compiled
behaviour nothing checks. `ir/natives_host.py` binds the nine symbols through
`os`, MARSHALLING each pointer across the boundary between a host object and
interpreter memory. The write-back half of that marshalling is the subtle one:
without it a read fills a copy, and the program gets a buffer of the right
LENGTH full of zeroes with nothing raised.

**TEXT MODE TRANSLATES, so this does too.** CPython's `write_text` goes
through `open(mode="w")` and puts `\r\n` on the disk for every `\n` on
Windows; `read_text` turns them back. A program that writes with `write_text`
and reads with `read_bytes` can see the difference, so matching the oracle
means doing the translation rather than calling raw bytes "more honest".

**WHAT IS STILL OPEN is the flavour, and it is left open deliberately.** `Path`
here extends `PurePosixPath`, so `str(Path("a") / "b")` is `a/b` where CPython
on Windows answers `a\b`. Closing it means a real Windows flavour -- drive
letters, UNC names, case-insensitive comparison, both separators accepted --
and doing the SEPARATOR alone would be worse than not doing it at all:
`Path("C:/x").is_absolute()` would still answer False while the class looked
native, which is the plausible-wrong-answer this document exists to prevent.
`as_posix()` agrees on both today.

**THE FLAVOUR IS THE NEXT THING TO WRITE, and it is worth more than it looks**,
because `cwd`, `home`, `absolute` and `resolve` are all waiting behind it and
those are the compiler's own most-wanted -- `resolve` 14 uses, `absolute` 7,
the two largest entries in the count at the top of this section. The CALL is
already reachable: `GetCurrentDirectoryA` takes a buffer and a `bytearray` is
one. It is the ANSWER that cannot be received -- `C:\Users\...` read by a
POSIX parser is a relative path whose first name is `C:`. So the four refuse
by name today and unblock together, which is one piece of work rather than
five.

What stays refused past that is `stat`, `iterdir`, `glob`, `rglob` and `walk`,
and they are blocked on something else entirely: a `struct stat` or a
`WIN32_FIND_DATA` has to come back, and a native call here returns one machine
word with no layout declared anywhere. That needs `ctypes.Structure` in
`cffi.py`, not more of this module. `open` needs an `io`.

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

## What `enum` found in the compiler

A metaclass is the one thing an enum cannot be written without, and almost
nothing had exercised one. Seven faults, all fixed, none of them about enums:

* **`C()` ANSWERED None for any class with a metaclass.** Every metaclass
  inherits `type.__call__`, so one writing no `__call__` of its own still found
  a hook -- and `_invoke_obj` has no case for that Native the way `_invoke`
  does, so it called its body directly. That is every metaclass, `ABCMeta`
  included.
* **`len(C)`, `x in C` and `repr(C)` did not consult the metaclass.**
  `__iter__` had grown the case and the other three had not, so `for c in
  Colour` worked while `len(Colour)` said `object of type 'EnumMeta' has no
  len()` -- about a class whose metaclass plainly defines one.
* **The class statement bound a SECOND HANDLE to the class the metaclass
  returned.** `is` compares handles, so `C is Meta.seen` was False for a class
  the metaclass had just handed back, and every enum member was built against
  the class its metaclass saw rather than the one the statement bound. The same
  rule `_apy_exc_type` states for `OSError is OSError`, missed one call away.
* **`"%d" % obj` reached `object.__format__`** and reported `unsupported format
  string passed to Instance.__format__`, naming a dunder the user never wrote
  and `%` never consults -- CPython's `%d` asks `__index__`. `"{:03d}".format`
  failed separately, because Python's own formatting machinery asks the HOST
  object and `Instance` had no `__format__` at all.
* **`"the " + obj` never tried `__radd__`**, and **`a | b` on two instances
  never tried `__or__`**: both were refused by a kind rule that ran BEFORE the
  reflected dispatch that would have handled them. The dispatch was already
  correct; nothing reached it.
* **`sorted`, `min` and `max` bypassed the user's `__lt__`.** The `<` operator
  falls back to it and these called `apy_order` directly, so `Num.THREE <
  Num.ONE` worked and `sorted([Num.THREE, Num.ONE])` said `unsupported operand
  type(s) for <` -- for the same two objects.

## What `dataclasses` found in the compiler

Specified first, in five parallel slices measured against CPython, with an
adversarial critic behind them -- which is why the module was right on the
first compile rather than the fifth. What it then found was the compiler, and
three of the five are things no earlier module had reached:

* **`type(name, bases, {"__annotations__": ...})` LOST THE ANNOTATIONS**, and
  so did assigning `C.__annotations__` afterwards: both read back `{}`. The
  runtime's `__annotations__` read consulted ONLY the PEP 649 `__annotate__`
  thunk and ignored a stored entry, while `setattr` wrote one perfectly
  happily -- so the write appeared to succeed and the read never saw it. That
  is how every library that builds a class dynamically declares its fields.
  **Fixed on both runtimes** (what was stored wins, the thunk is the fallback),
  and `make_dataclass` went from refused to covered as a result. Refusing it
  was the right answer while the bug stood and the wrong thing to leave
  standing.
* **KEYWORD-ONLY PARAMETERS ARE NOT ENFORCED AT RUN TIME.** `field(3)` against
  `def field(*, ...)` is accepted and becomes the `default`; the compiler warns
  where it can see the call and lets it through. One permissive superset rather
  than a wrong answer, and stated in the module.
* **A comparison against a user object named the wrong class.** `a < b` where
  `__lt__` answered NotImplemented reported `'<' not supported between
  instances of 'Instance' and 'int'` -- `Instance` being the interpreter's own
  class, which every user object shares. The arithmetic path had rewritten that
  message and the comparison path had not, so `a + b` blamed the right types
  and `a < b` did not. Fixed.
* **`bytearray` cannot be used as a VALUE** (`E0056`), only inside an
  `isinstance` call, so a tuple of mutable types has to be written as separate
  tests.
* **`__class__` does not exist on an `int`.** CPython's generated `__eq__`
  tests `other.__class__ is self.__class__` deliberately -- it differs from
  `type()` for anything overriding `__class__` -- and comparing a dataclass to
  an integer raised `AttributeError` from inside `__eq__` where CPython answers
  NotImplemented. The module keeps CPython's spelling and falls back.

PEP 649 also caught the implementation out: `__annotations__` is never in
`cls.__dict__` under 3.14, so the `__dict__` lookup that used to be the careful
way to read a class's OWN annotations now answers `{}` for every class -- and
the first version produced dataclasses with no fields at all. The plain
attribute read is correct on both runtimes and was verified on both.

## What the adversarial critic caught that the specification missed

The five slices were commissioned to specify `dataclasses` before it was
written; a sixth agent was told to find what they had MISSED. It earned its
place three times over, and every one of the three is a WRONG ANSWER that no
happy-path test notices:

* **`asdict` deep-copies leaf values.** The recursion set is exactly
  dataclass / list / tuple / dict, and everything else goes through
  `deepcopy`. Passing leaves through gives a dict whose values are the SAME
  objects the instance holds, so mutating the snapshot mutates the original --
  which is precisely what somebody calling `asdict` is trying to avoid.
* **`replace()` must let an unknown keyword reach the constructor.** Building
  a fresh dict of known fields silently DROPPED it, so `replace(cfg,
  tiemout=5)` answered an unchanged object and nobody found out. CPython's
  message names the class, which only the constructor can do.
* **A namedtuple stays a namedtuple**, in both `asdict` and `astuple`, and is
  rebuilt SPLATTED (`NT(*[...])`) because its constructor takes one argument
  per field. Untestable here until `collections` is rebuilt, and said so in
  the module rather than left looking covered.

It also confirmed two things right by construction: a SET is not recursed into
(its members are deep-copied instances, and converting them to dicts would
make them unhashable), and `asdict`/`astuple`/`replace` take instances only,
never a class.

**And `dataclasses` then found a bug in `copy`.** `deepcopy` of a FROZEN
dataclass raised `FrozenInstanceError` from the class's own `__setattr__` --
while the copy was being CONSTRUCTED, not mutated. `_reconstruct` restores
state with `object.__setattr__` now, which is what CPython's `__dict__`
restore amounts to. Every program that freezes a dataclass and copies it hit
this.

## A bundled module's exception class leaks its mangled name

Found by restoring `copy`, which defines `class Error(Exception)`. Two
symptoms, one cause, and both are the compiler rather than the module:

    import copy
    issubclass(copy.Error, Exception)      # False; CPython says True
    try: raise copy.Error("x")
    except Exception as e: type(e).__name__   # `_asmpy_bundled_copy_Error`

A bundled module's classes are spliced under mangled names and the splice
restores `__name__` afterwards, precisely so the mangling stays invisible --
`bundled.py` says so where it emits that assignment. It did not stay invisible:
the runtime's exception hierarchy is keyed by NAME and was registered with the
mangled one, so the class sat outside the tree `issubclass` walks.

**THE `issubclass` HALF IS FIXED, on both runtimes.** The answer was not to
re-key the hierarchy -- that would conflate two bundled modules both defining
`Error`, and `copy.Error` and `re.error` are distinct classes in CPython. It
was to make the REGISTRATION FOLLOW THE RENAME: assigning `__name__` on a class
that is a registered exception re-registers it under the new spelling, keeping
BOTH, because generated code still raises through the mangled one. See
`objects_host._rename_exception` and the `__name__` arm of the C's
`apy_setattr`.

**THE PRINTED TYPE STILL LEAKS.** `type(e).__name__` for a bundled exception
answers `_asmpy_bundled_copy_Error` where CPython says `Error`, on both paths.
Catching it, `issubclass` and `isinstance` are all correct; what is wrong is
the name a traceback or a `print(type(e))` shows.

**An attempt at it was made and BACKED OUT**, and what it established is worth
more than the attempt was:

* The cell's name CANNOT simply be corrected. `except copy.Error` compiles to
  the mangled spelling and `apy_error_matches` walks the hierarchy by name, so
  renaming the cell fixes the display and breaks the catch. Any fix has to
  separate the name a program MATCHES on from the name it SHOWS.
* `type(e).__name__` is FUSED by the frontend into a single `apy_type_name`
  call (`dynamic.py`, the `ast.Attribute(attr="__name__")` case), which reads
  the cell's name string directly. That is why `t = type(e); t.__name__`
  answers `Error` and `type(e).__name__` does not, for the same object -- only
  one of them goes through the class.
* A display-name helper mapping the cell's name back through the registered
  class is the shape of the fix, and it did not work when tried: the lookup
  found nothing, which means a bundled module's exception class is not in
  `apy_exc_class_table` under the name its cells carry. THAT is the thing to
  establish first, and it was not established, so nothing was kept.

There are at least four display paths (`apy_type_name`, the `repr` and `str`
builders, `apy_kind_name`), so a partial fix makes the two runtimes disagree
with each other as well as with CPython -- which is why the attempt was
reverted whole rather than left half-applied.

`inspect` found a third, worked around rather than fixed: **`X = X` in a class
body reads the right-hand side as the class attribute being defined** instead
of the enclosing module's name, which is valid Python and how CPython's own
`inspect` writes `POSITIONAL_ONLY = POSITIONAL_ONLY`. The module uses
underscored module-level names instead and says so.

## What it cost to start

Recorded so each module restored is measured against a real baseline rather
than an impression.

| | conformance (spec+cpython) |
| --- | --- |
| before archiving | 1668/1668 (100%) |
| after archiving, with 8 modules rebuilt | 1627/1668 (97.5%) |

**41 cases, and not one of them is a wrong answer.** That is the number worth
having, because it says the clear cost coverage and not correctness:

| | | |
| --- | --- | --- |
| 35 | REFUSED | the program names a module that no longer exists |
| 5 | FAIL | four reach an archived module at RUN time (`sys.monitoring`, `sys.addaudithook`, `typing`, `inspect`); one was `__future__`, below |
| 1 | TIMEOUT | not a regression, see below |

The 35 refusals name exactly what to write next, in this order by how many
cases each unblocks: `collections` and `collections.abc`, `abc`, `typing`,
`dataclasses`, `io`, `os` (`pathlib` is written -- see above), `enum`,
`numbers`, `decimal`,
`fractions`, `statistics`, `datetime`/`zoneinfo`, `contextvars`, `copy`,
`unicodedata`, `tomllib`, `annotationlib`.

**The TIMEOUT is a measurement artefact and was checked rather than assumed.**
`exceptions/type-raised-by-builtin-ops` calls `eval` in a loop; it passes in
isolation and takes ~18s, against a 30s default timeout, so under `-j 2` it
tipped over. The cost is the embedded compiler -- `eval` splices roughly 3,300
lines of `_pylex`, `_pyparse`, `_pyvalidate`, `_pycompile` and `_pyrun` into
the program. I suspected the `functools` import I had just added to
`warnings`, measured it at 17.4s with and 17.2s without, and it was not that.
The import is gone anyway (see `warnings._wraps`) on the smaller argument that
a module spliced into every `eval` program should not carry a dependency it
uses three lines of.

**One case found a file that had been LOST rather than archived.**
`pep/0236-future-statement` failed with `'int' object has no attribute
'optional'`, which turned out to be `bundled/__future__.py` deleted by the
clear and never copied into `archived/stdlib-prerefactor/` -- so unlike the
other twenty-seven it had no reference version, and the only copy was in git.
It is restored, with a coverage line and a test it never had. Every other
deleted file did reach the archive; that was checked, not assumed.
